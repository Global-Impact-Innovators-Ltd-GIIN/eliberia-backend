"""Messages, notification count, certificate wallet / revocation, admin service management, USSD."""
import hmac
import re

from flask import Blueprint, Response, current_app, g, jsonify, request
from sqlalchemy import func

from ..extensions import db
from ..models import (Application, ApplicationEvent, Certificate, Institution, Message, Notification,
                      Service, User)
from ..services import certificates
from ..rules import normalize_doc_rules
from ..services import forms, identity
from ..utils import audit, auth_required, err, need, normalize_phone, notify, rate_limit
from .applications import _own

bp = Blueprint("extras", __name__, url_prefix="/api")


# ---------------- messages on an application ----------------
@bp.get("/applications/<int:app_id>/messages")
@auth_required()
def list_messages(app_id):
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    rows = Message.query.filter_by(application_id=a.id).order_by(Message.id).all()
    return jsonify([m.to_dict(g.user.id) for m in rows])


@bp.post("/applications/<int:app_id>/messages")
@auth_required()
@rate_limit("message", 60, 600)
def post_message(app_id):
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    body = ((request.get_json(silent=True) or {}).get("body") or "").strip()
    if not 2 <= len(body) <= 1000:
        return err("Write a message between 2 and 1000 characters")
    m = Message(application_id=a.id, sender_id=g.user.id, sender_role=g.user.role,
                sender_name=g.user.full_name, body=body)
    db.session.add(m)
    if g.user.role in ("citizen", "agent"):
        if a.assigned_to:
            notify(a.assigned_to, f"New message on {a.reference}", body[:120])
    else:
        notify(a.user_id, f"Message from {a.service.institution.name}", body[:120])
    audit("message_sent", a.reference)
    db.session.commit()
    return jsonify(m.to_dict(g.user.id)), 201


# ---------------- notifications ----------------
@bp.get("/notifications/count")
@auth_required()
def unread_count():
    n = Notification.query.filter_by(user_id=g.user.id, channel="app", read=False).count()
    return jsonify({"unread": n})


# ---------------- certificate wallet and revocation ----------------
@bp.get("/certificates")
@auth_required("citizen")
def my_certificates():
    rows = (db.session.query(Certificate, Application).join(Application, Application.id == Certificate.application_id)
            .filter(Application.user_id == g.user.id).order_by(Certificate.id.desc()).all())
    return jsonify([{**c.to_dict(), "reference": a.reference, "application_id": a.id} for c, a in rows])


@bp.post("/staff/certificates/<number>/revoke")
@auth_required("officer", "inst_admin", "admin")
def revoke_certificate(number):
    cert = Certificate.query.filter_by(number=number.strip().upper()).first()
    a = _own(cert.application_id) if cert else None
    if not cert or not a:
        return err("Certificate not found", 404)
    reason = ((request.get_json(silent=True) or {}).get("reason") or "").strip()
    if len(reason) < 5:
        return err("Give a reason for revoking this certificate")
    if cert.revoked:
        return err("This certificate is already revoked", 409)
    cert.revoked = True
    db.session.add(ApplicationEvent(application=a, status=a.status, actor=g.user.full_name,
                                    message=f"Certificate {cert.number} revoked: {reason[:200]}"))
    notify(a.user_id, "Certificate revoked", f"{cert.number} is no longer valid. Reason: {reason[:150]}")
    audit("certificate_revoked", cert.number)
    db.session.commit()
    return jsonify(cert.to_dict())


# ---------------- admin: manage the service catalog ----------------
FIELD_TYPES = set(forms.FIELD_TYPES)


def _slug(label):
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "field"


def _apply_service(s, d, creating, lock_institution=None):
    """Validate and copy editable properties onto a Service. Returns an error message or None."""
    name = (d.get("name", s.name) or "").strip()
    category = (d.get("category", s.category) or "").strip()
    if len(name) < 3 or len(category) < 2:
        return "Name and category are required"
    inst = db.session.get(Institution, lock_institution or d.get("institution_id", s.institution_id) or 0)
    if not inst:
        return "Choose a valid institution"
    try:
        fee = float(d.get("fee_usd", s.fee_usd or 0))
        sla = int(d.get("sla_days", s.sla_days or 5))
    except (TypeError, ValueError):
        return "Fee and processing days must be numbers"
    if not 0 <= fee <= 10000 or not 1 <= sla <= 365:
        return "Fee must be 0-10000 and processing time 1-365 days"
    docs = d.get("required_documents", s.required_documents or [])
    if not isinstance(docs, list) or len(docs) > 10:
        return "required_documents must be a list of up to 10 documents"
    for it in docs:
        if isinstance(it, dict):
            if not str(it.get("name") or "").strip():
                return "Every required document needs a name"
            if not [x for x in (it.get("formats") or []) if x in ("pdf", "jpg", "png")]:
                return f"Choose at least one accepted format for '{it['name']}'"
            try:
                if not 0.1 <= float(it.get("max_mb", 5)) <= 8:
                    raise ValueError
            except (TypeError, ValueError):
                return f"The size limit for '{it['name']}' must be between 0.1 and 8 MB"
    doc_rules = normalize_doc_rules(docs)
    if len({r["name"] for r in doc_rules}) != len(doc_rules):
        return "Two required documents share the same name"
    fields_in = d.get("form_fields", s.form_fields or [])
    if not isinstance(fields_in, list) or len(fields_in) > 25:
        return "form_fields must be a list of up to 25 fields"
    fields, seen = [], set()
    for f in fields_in:
        label = str(f.get("label") or "").strip()[:80]
        ftype = f.get("type", "text")
        if not label or ftype not in FIELD_TYPES:
            return "Every question needs a label and a valid answer type"
        fname = f.get("name") or _slug(label)
        if fname in seen:
            return f"Two fields share the name '{fname}'"
        seen.add(fname)
        item = {"name": fname, "label": label, "type": ftype, "required": bool(f.get("required", True))}
        if ftype == "select":
            opts = [str(o).strip()[:60] for o in (f.get("options") or []) if str(o).strip()]
            if len(opts) < 2:
                return f"'{label}' needs at least two options"
            item["options"] = opts
        if ftype == "identity":
            docs_ok = [x for x in (f.get("doc_types") or []) if x in identity.DOC_TYPES]
            countries_ok = [c for c in (f.get("countries") or []) if c in identity.COUNTRIES]
            if docs_ok and len(docs_ok) < len(identity.DOC_TYPES):
                item["doc_types"] = docs_ok
            if countries_ok:
                item["countries"] = countries_ok[:20]
            if item.get("doc_types") == [identity.NATIONAL_ID] and countries_ok and countries_ok != [identity.HOME]:
                return f"'{label}': a National ID can only be asked of Liberian nationals"
        if str(f.get("help") or "").strip():
            item["help"] = str(f["help"]).strip()[:200]
        if f.get("max_length"):
            try:
                item["max_length"] = max(1, min(int(f["max_length"]), 300))
            except (TypeError, ValueError):
                return f"'{label}': maximum length must be a number"
        fields.append(item)
    s.name, s.category, s.institution_id, s.fee_usd, s.sla_days = name[:200], category[:60], inst.id, fee, sla
    s.description = str(d.get("description", s.description or ""))[:1000]
    s.requires_face = bool(d.get("requires_face", s.requires_face))
    s.requires_fingerprint = bool(d.get("requires_fingerprint", s.requires_fingerprint))
    s.required_documents = doc_rules
    s.seed_version = 99  # hand-edited: seed upgrades must never overwrite it
    s.form_fields = fields
    s.active = bool(d.get("active", True if creating else s.active))
    return None


def _admin_dict(s):
    return {**s.to_dict(), "active": s.active, "institution_id": s.institution_id}


@bp.get("/admin/services")
@auth_required("admin")
def admin_services():
    return need("services") or jsonify([_admin_dict(s) for s in Service.query.order_by(Service.category, Service.name)])


@bp.post("/admin/services")
@auth_required("admin")
def admin_create_service():
    gate = need("services")
    if gate:
        return gate
    d = request.get_json(silent=True) or {}
    code = re.sub(r"[^A-Z0-9_]", "", str(d.get("code") or "").upper().replace(" ", "_"))[:40]
    if len(code) < 3:
        return err("Give the service a code of at least 3 letters, e.g. FISHING_LICENSE")
    if Service.query.filter_by(code=code).first():
        return err("A service with this code already exists", 409)
    s = Service(code=code, name="", category="", institution_id=0)
    problem = _apply_service(s, d, creating=True)
    if problem:
        return err(problem)
    db.session.add(s)
    audit("service_created", code)
    db.session.commit()
    return jsonify(_admin_dict(s)), 201


@bp.put("/admin/services/<code>")
@auth_required("admin")
def admin_update_service(code):
    gate = need("services")
    if gate:
        return gate
    s = Service.query.filter_by(code=code).first()
    if not s:
        return err("Service not found", 404)
    problem = _apply_service(s, request.get_json(silent=True) or {}, creating=False)
    if problem:
        db.session.rollback()
        return err(problem)
    audit("service_updated", code)
    db.session.commit()
    return jsonify(_admin_dict(s))


# ---------------- USSD (feature phones) ----------------
MENU = "Welcome to eLiberia\n1. Track application\n2. My applications\n3. Check a service fee\n4. Verify certificate"
LABEL = {"draft": "Not yet submitted", "submitted": "Received", "in_review": "Being reviewed",
         "info_requested": "More information needed", "biometric_pending": "Identity check needed",
         "approved": "Approved", "rejected": "Rejected", "ready_for_collection": "Ready for collection",
         "completed": "Completed"}


def _compact(v):
    return re.sub(r"[^A-Z0-9]", "", (v or "").upper())


def _ussd_reply(text, phone, trusted):
    parts = [p.strip() for p in text.split("*")] if text else []
    if not parts:
        return "CON " + MENU
    choice, arg = parts[0], (parts[1] if len(parts) > 1 else None)
    if choice == "1":
        if arg is None:
            return "CON Enter your bill ID or application reference:"
        key = _compact(arg)
        a = (Application.query.filter_by(bill_id=key).first()
             or Application.query.filter(func.replace(Application.reference, "-", "") == key).first())
        if not a:
            return "END No application found. Check the number and try again."
        pay = "" if a.fee_usd <= 0 else (" Paid." if a.paid else f" Unpaid: ${a.fee_usd:.2f}.")
        return f"END {a.service.name}\nStatus: {LABEL.get(a.status, a.status)}.{pay}"
    if choice == "2":
        if not trusted:
            return "END This option only works when you dial from your own phone."
        u = User.query.filter_by(phone=normalize_phone(phone) or "").first()
        rows = (Application.query.filter_by(user_id=u.id).order_by(Application.id.desc()).limit(3).all()
                if u else [])
        if not rows:
            return "END You have no applications yet. Visit an eLiberia agent to apply."
        return "END " + "\n".join(f"{a.service.name[:28]}: {LABEL.get(a.status, a.status)}" for a in rows)
    if choice == "3":
        if arg is None:
            return "CON Type the service name, e.g. passport:"
        words = [w for w in re.findall(r"[a-z]+", arg.lower()) if len(w) > 2]
        best, score = None, 0
        for s in Service.query.filter_by(active=True):
            sc = sum(1 for w in words if w in s.name.lower())
            if sc > score:
                best, score = s, sc
        if not best:
            return "END No matching service. Try a shorter word."
        fee = f"${best.fee_usd:.2f}" if best.fee_usd else "Free"
        return f"END {best.name}\nFee: {fee}\nTime: about {best.sla_days} working days"
    if choice == "4":
        if arg is None:
            return "CON Enter the certificate number:"
        key = _compact(arg)
        c = Certificate.query.filter(func.replace(Certificate.number, "-", "") == key).first()
        if not c or not certificates.is_authentic(c):
            return "END NOT VERIFIED. Do not accept this document."
        if c.revoked:
            return "END REVOKED. This certificate is no longer valid."
        return f"END GENUINE\n{c.service_name}\nHolder: {c.holder_name}\nIssued: {c.issued_at:%d %b %Y}"
    return "END Invalid choice. Dial again."


@bp.post("/ussd")
@rate_limit("ussd", 120, 600)
def ussd():
    """USSD callback in the common aggregator format (sessionId, phoneNumber, text joined by '*').
    Replies are plain text starting with CON (expects more input) or END (final screen).
    The caller's number is only trusted when the gateway presents the shared secret."""
    d = request.form or (request.get_json(silent=True) or {})
    secret = current_app.config.get("USSD_SECRET")
    trusted = bool(secret) and hmac.compare_digest(request.args.get("key", ""), secret)
    reply = _ussd_reply(str(d.get("text") or ""), str(d.get("phoneNumber") or ""), trusted)
    return Response(reply[:182 + 4], mimetype="text/plain")
