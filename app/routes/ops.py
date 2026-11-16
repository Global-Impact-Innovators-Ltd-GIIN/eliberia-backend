"""Payment confirmation and reconciliation, daily tasks, and launch-readiness status."""
import csv
import hmac
import io
import json
from datetime import date, datetime, time, timedelta

from flask import Blueprint, Response, current_app, g, jsonify, request

from ..extensions import db
from ..models import Application, ApplicationEvent, Institution, Payment, Service, User, now
from ..services import payments, sms, storage, tasks
from ..utils import audit, auth_required, err, has_perm, notify, rate_limit
from .applications import _own

bp = Blueprint("ops", __name__, url_prefix="/api")


def _paid_notice(p):
    notify(p.application.user_id, "Payment received", f"${p.amount_usd:.2f} for {p.application.reference}")
    audit("payment_success", p.txn_ref)


# ---------------- citizen / agent: check a pending payment ----------------
@bp.get("/applications/payments/<txn_ref>/status")
@auth_required("citizen", "agent")
@rate_limit("pay_status", 120, 600)
def payment_status(txn_ref):
    p = Payment.query.filter_by(txn_ref=txn_ref).first()
    if not p or not _own(p.application_id):
        return err("Payment not found", 404)
    if payments.refresh(p):
        if p.status == "success":
            _paid_notice(p)
        db.session.commit()
    return jsonify(p.to_dict())


# ---------------- providers ----------------
@bp.post("/payments/webhook")
@rate_limit("pay_webhook", 300, 600)
def payment_webhook():
    """Signed confirmation from a payment aggregator: body {txn_ref, status, amount, provider_ref},
    header X-Signature = hex HMAC-SHA256(body, PAYMENT_WEBHOOK_SECRET)."""
    raw = request.get_data()
    if not payments.verify_webhook(raw, request.headers.get("X-Signature", "")):
        return err("Invalid signature", 401)
    try:
        d = json.loads(raw)
    except ValueError:
        return err("Invalid JSON")
    p = Payment.query.filter_by(txn_ref=str(d.get("txn_ref") or "")).first()
    if not p:
        return err("Unknown payment", 404)
    if p.status != "pending":
        return jsonify({"ok": True, "status": p.status})  # retries are harmless
    ok = str(d.get("status") or "").lower() in ("success", "successful", "paid", "completed")
    if ok and abs(float(d.get("amount") or 0) - p.amount_usd) > 0.009:
        audit("payment_amount_mismatch", f"{p.txn_ref} got {d.get('amount')}")
        db.session.commit()
        return err("Amount does not match the bill", 409)
    payments.settle(p, ok, provider_ref=str(d.get("provider_ref") or "")[:80] or None)
    if ok:
        _paid_notice(p)
    else:
        audit("payment_failed", p.txn_ref)
    db.session.commit()
    return jsonify({"ok": True, "status": p.status})


@bp.route("/payments/mtn/callback", methods=["POST", "PUT"])
@rate_limit("mtn_cb", 300, 600)
def mtn_callback():
    """MTN calls this when a request-to-pay finishes. The body is not trusted: we ask MTN for the status."""
    d = request.get_json(silent=True) or {}
    ref = request.headers.get("X-Reference-Id") or d.get("referenceId") or ""
    ext = d.get("externalId") or ""
    p = (Payment.query.filter_by(provider_ref=ref).first() if ref else None) or \
        (Payment.query.filter_by(txn_ref=ext).first() if ext else None)
    if p and payments.refresh(p):
        if p.status == "success":
            _paid_notice(p)
        db.session.commit()
    return jsonify({"ok": True})


# ---------------- staff: refunds and reconciliation ----------------
def _finance_scope():
    """(allowed, institution_id or None for all)."""
    u = g.user
    if u.role == "admin":
        return has_perm(u, "finance"), None
    if u.role == "inst_admin":
        return True, u.institution_id
    return False, None


def _payments_query(inst_id):
    q = Payment.query.join(Application, Application.id == Payment.application_id).join(
        Service, Service.id == Application.service_id)
    if inst_id:
        q = q.filter(Service.institution_id == inst_id)
    return q


@bp.post("/staff/payments/<txn_ref>/refund")
@auth_required("admin", "inst_admin")
def refund(txn_ref):
    allowed, inst_id = _finance_scope()
    if not allowed:
        return err("Your account does not have the 'finance' permission", 403)
    p = _payments_query(inst_id).filter(Payment.txn_ref == txn_ref).first()
    if not p:
        return err("Payment not found", 404)
    if p.status != "success":
        return err("Only a successful payment can be refunded", 409)
    reason = str((request.get_json(silent=True) or {}).get("reason") or "").strip()
    if len(reason) < 5:
        return err("Give a reason for the refund")
    p.status, p.refunded_at, p.refunded_by, p.refund_reason = "refunded", now(), g.user.id, reason[:300]
    a = p.application
    db.session.add(ApplicationEvent(application=a, status=a.status, actor=g.user.full_name,
                                    message=f"Payment {p.txn_ref} refunded: {reason[:200]}"))
    notify(a.user_id, "Refund issued", f"${p.amount_usd:.2f} for {a.reference} is being returned. Reason: {reason[:150]}")
    audit("payment_refunded", p.txn_ref)
    db.session.commit()
    return jsonify(p.to_dict())


@bp.get("/staff/payments")
@auth_required("admin", "inst_admin")
def reconciliation():
    allowed, inst_id = _finance_scope()
    if not allowed:
        return err("Your account does not have the 'finance' permission", 403)
    try:
        start = date.fromisoformat(request.args.get("from") or (now().date() - timedelta(days=30)).isoformat())
        end = date.fromisoformat(request.args.get("to") or now().date().isoformat())
    except ValueError:
        return err("Dates must be YYYY-MM-DD")
    if g.user.role == "admin" and request.args.get("institution"):
        inst = Institution.query.filter_by(code=request.args["institution"]).first()
        inst_id = inst.id if inst else -1
    q = _payments_query(inst_id).filter(Payment.created_at >= datetime.combine(start, time.min),
                                        Payment.created_at <= datetime.combine(end, time.max))
    if request.args.get("status"):
        q = q.filter(Payment.status == request.args["status"])
    rows = q.order_by(Payment.created_at.desc()).limit(5000).all()
    items, totals, by_method, by_day = [], {}, {}, {}
    for p in rows:
        a = p.application
        items.append({**p.to_dict(), "reference": a.reference, "bill_id": a.bill_id, "service": a.service.name,
                      "institution": a.service.institution.name, "applicant": a.user.full_name})
        totals[p.status] = round(totals.get(p.status, 0) + p.amount_usd, 2)
        if p.status == "success":
            by_method[p.method] = round(by_method.get(p.method, 0) + p.amount_usd, 2)
            k = p.created_at.date().isoformat()
            by_day[k] = round(by_day.get(k, 0) + p.amount_usd, 2)
    if request.args.get("format") == "csv":
        out = io.StringIO()
        cols = ["created_at", "txn_ref", "reference", "bill_id", "institution", "service", "applicant", "method",
                "provider", "amount_usd", "currency", "status", "provider_ref", "refund_reason"]
        w = csv.DictWriter(out, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in items:
            w.writerow({k: r.get(k) for k in cols})
        audit("payments_export", f"{start}..{end}")
        db.session.commit()
        return Response(out.getvalue(), mimetype="text/csv", headers={
            "Content-Disposition": f'attachment; filename="eliberia-payments-{start}-{end}.csv"'})
    return jsonify({"from": start.isoformat(), "to": end.isoformat(), "count": len(items), "totals": totals,
                    "by_method": by_method, "by_day": dict(sorted(by_day.items())), "items": items[:500]})


# ---------------- daily tasks ----------------
@bp.post("/tasks/daily")
@rate_limit("tasks", 10, 3600)
def daily_from_scheduler():
    """Called once a day by the scheduled GitHub Action (header X-Tasks-Key = TASKS_SECRET)."""
    secret = current_app.config.get("TASKS_SECRET")
    if not secret:
        return err("Not found", 404)
    if not hmac.compare_digest(request.headers.get("X-Tasks-Key", ""), secret):
        return err("Forbidden", 403)
    return jsonify(tasks.run_daily())


@bp.post("/admin/tasks/daily")
@auth_required("admin")
def daily_from_admin():
    if not has_perm(g.user, "all"):
        return err("Only a super administrator can run maintenance", 403)
    result = tasks.run_daily()
    audit("daily_tasks_run", json.dumps(result))
    db.session.commit()
    return jsonify(result)


@bp.get("/meta/payments")
def payments_meta():
    """Which provider handles each method, so the page can say whether payments are simulated."""
    return jsonify({m: payments.provider_for(m) for m in ("orange_money", "mtn_momo", "agent_cash")})


# ---------------- launch readiness ----------------
@bp.get("/admin/system")
@auth_required("admin")
def system_status():
    cfg = current_app.config
    demo = [u.phone for u in User.query.filter(User.is_demo.is_(True), User.disabled.isnot(True))]
    checks = [
        {"key": "sms", "label": "SMS gateway", "ok": sms.status()["live"], "detail": sms.status()},
        {"key": "payments", "label": "Real payments", "ok": payments.status()["live"], "detail": payments.status()},
        {"key": "storage", "label": "Permanent file storage", "ok": storage.status()["durable"], "detail": storage.status()},
        {"key": "codes_hidden", "label": "Codes no longer shown on screen", "ok": not cfg.get("EXPOSE_OTP"), "detail": {}},
        {"key": "demo_accounts", "label": "Demo accounts disabled", "ok": not demo, "detail": {"active_demo_accounts": len(demo)}},
        {"key": "data_key", "label": "Encryption key unchanged", "ok": not cfg.get("DATA_KEY_MISMATCH"),
         "detail": {"explicit_key": bool(cfg.get("DATA_KEY"))}},
        {"key": "backups", "label": "Daily backups configured", "ok": bool(cfg.get("TASKS_SECRET")),
         "detail": {"hint": "Set TASKS_SECRET and the GitHub Action secrets"}},
        {"key": "database", "label": "Permanent database", "ok": not str(cfg["SQLALCHEMY_DATABASE_URI"]).startswith("sqlite"),
         "detail": {"note": "Render's free PostgreSQL expires after 30 days"}},
    ]
    return jsonify({"checks": checks, "ready": all(c["ok"] for c in checks)})
