import hashlib
import os
import uuid
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, request, send_file

from ..extensions import db
from ..models import (Appointment, Application, ApplicationEvent, BiometricRecord, Document, Feedback,
                      Payment, Service, now)
from ..rules import check_file, normalize_doc_rules
from ..services import biometrics, forms, payments, vault
from ..utils import audit, auth_required, err, gen_bill_id, gen_reference, notify, rate_limit, record_access
from .catalog import CENTERS

bp = Blueprint("applications", __name__, url_prefix="/api/applications")

ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg"}


def _log(app_, status, message, actor=None):
    app_.status = status
    db.session.add(ApplicationEvent(application=app_, status=status, message=message,
                                    actor=actor or g.user.full_name))


def _own(app_id):
    """The application, if the signed-in user may see it: its citizen, the agent who assisted,
    an officer of the owning institution, or an admin."""
    a = db.session.get(Application, app_id)
    if not a:
        return None
    u = g.user
    allowed = ((u.role == "citizen" and a.user_id == u.id)
               or (u.role == "agent" and a.agent_id == u.id)
               or (u.role in ("officer", "inst_admin") and a.service.institution_id == u.institution_id)
               or u.role == "admin")
    return a if allowed else None


def _validate(service, data):
    missing = [f["label"] for f in (service.form_fields or [])
               if f.get("required") and not str(data.get(f["name"], "")).strip()]
    return missing


@bp.post("")
@auth_required("citizen", "agent")
def create():
    d = request.get_json(silent=True) or {}
    owner_id, agent_id = g.user.id, None
    if g.user.role == "agent":
        from .agent import active_consent
        try:
            owner_id = int(d.get("citizen_id"))
        except (TypeError, ValueError):
            return err("citizen_id is required")
        if not active_consent(g.user.id, owner_id):
            return err("You do not have this citizen's consent. Ask them for a new code.", 403)
        agent_id = g.user.id
    s = Service.query.filter_by(code=d.get("service_code"), active=True).first()
    if not s:
        return err("Service not found", 404)
    ben = d.get("beneficiary") or None
    if ben:
        name = str(ben.get("name") or "").strip()[:150]
        if len(name) < 3:
            return err("Enter the full name of the person you are applying for")
        ben = {"name": name, "relation": str(ben.get("relation") or "Other")[:40]}
    a = Application(reference=gen_reference(), user_id=owner_id, agent_id=agent_id, service=s,
                    data=d.get("data") or {}, fee_usd=s.fee_usd, beneficiary=ben,
                    bill_id=gen_bill_id() if s.fee_usd > 0 else None)
    db.session.add(a)
    _log(a, "draft", "Application started" + (" with agent assistance" if agent_id else ""))
    audit("application_create", a.reference)
    db.session.commit()
    return jsonify(a.to_dict(full=True)), 201


@bp.get("")
@auth_required("citizen")
def mine():
    items = Application.query.filter_by(user_id=g.user.id).order_by(Application.id.desc()).all()
    return jsonify([a.to_dict() for a in items])


@bp.get("/track/<reference>")
@rate_limit("track", 60, 600)
def track(reference):
    """Public status tracking by reference or bill ID (no personal data returned)."""
    key = reference.strip().upper()
    a = (Application.query.filter_by(reference=key).first()
         or Application.query.filter_by(bill_id=key).first())
    if not a:
        return err("No application found with that reference or bill ID", 404)
    return jsonify({"reference": a.reference, "service": a.service.name, "status": a.status,
                    "institution": a.service.institution.name, "paid": a.paid, "fee_usd": a.fee_usd,
                    "due_at": a.due_at.isoformat() if a.due_at else None,
                    "updated_at": a.updated_at.isoformat(),
                    "events": [{"status": e.status, "message": e.message, "created_at": e.created_at.isoformat()}
                               for e in a.events]})


@bp.get("/<int:app_id>")
@auth_required()
def detail(app_id):
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    return jsonify(a.to_dict(full=True))


@bp.put("/<int:app_id>")
@auth_required("citizen", "agent")
def update(app_id):
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    if a.status not in ("draft", "info_requested"):
        return err("This application can no longer be edited", 409)
    a.data = (request.get_json(silent=True) or {}).get("data", a.data)
    db.session.commit()
    return jsonify(a.to_dict(full=True))


@bp.post("/<int:app_id>/documents")
@auth_required("citizen", "agent")
def upload_document(app_id):
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    f = request.files.get("file")
    if not f or not f.filename:
        return err("No file uploaded")
    if a.status not in ("draft", "info_requested"):
        return err("Documents can only be changed before the application is submitted", 409)
    ext = os.path.splitext(f.filename)[1].lower()
    doc_type = (request.form.get("doc_type") or "").strip()
    rules = normalize_doc_rules(a.service.required_documents)
    if rules:
        rule = next((r for r in rules if r["name"] == doc_type), None)
        if not rule:
            return err("Choose which required document this file is")
    else:
        rule = normalize_doc_rules([doc_type or "Supporting document"])[0]
        doc_type = rule["name"]
    raw = f.read()
    problem = check_file(rule, f.filename, raw)  # the institution's format and size rules
    if problem:
        return err(problem)
    for old in [x for x in a.documents if x.doc_type == doc_type]:  # a new upload replaces the old one
        try:
            os.remove(os.path.join(current_app.config["UPLOAD_DIR"], old.stored_name))
        except OSError:
            pass
        db.session.delete(old)
    stored = uuid.uuid4().hex + ext
    os.makedirs(current_app.config["UPLOAD_DIR"], exist_ok=True)
    with open(os.path.join(current_app.config["UPLOAD_DIR"], stored), "wb") as out:
        out.write(vault.encrypt_bytes(raw))  # encrypted at rest
    doc = Document(application=a, doc_type=doc_type,
                   filename=os.path.basename(f.filename)[:200], stored_name=stored,
                   sha256=hashlib.sha256(raw).hexdigest())
    db.session.add(doc)
    db.session.commit()
    return jsonify(doc.to_dict()), 201


@bp.get("/<int:app_id>/documents/<int:doc_id>")
@auth_required()
def download_document(app_id, doc_id):
    a = _own(app_id)
    doc = db.session.get(Document, doc_id)
    if not a or not doc or doc.application_id != a.id:
        return err("Document not found", 404)
    path = os.path.join(current_app.config["UPLOAD_DIR"], doc.stored_name)
    if not os.path.exists(path):
        return err("This file is no longer stored on the server", 410)
    if g.user.role in ("officer", "inst_admin", "admin"):
        record_access(a, f"Viewed document: {doc.doc_type}")
        db.session.commit()
    import io
    with open(path, "rb") as fh:
        data = vault.decrypt_bytes(fh.read())
    return send_file(io.BytesIO(data), download_name=doc.filename)


@bp.post("/<int:app_id>/submit")
@auth_required("citizen", "agent")
def submit(app_id):
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    if a.status not in ("draft", "info_requested"):
        return err("Application already submitted", 409)
    clean, problems = forms.validate(a.service, a.data or {})
    if problems:
        msg = problems[0] if len(problems) == 1 else f"{len(problems)} answers need attention. " + " ".join(problems[:4])
        return jsonify({"error": msg, "problems": problems}), 400
    have = {x.doc_type for x in a.documents}
    missing_docs = [r["name"] for r in normalize_doc_rules(a.service.required_documents)
                    if r["required"] and r["name"] not in have]
    if missing_docs:
        return err("Please upload: " + ", ".join(missing_docs))
    a.data = clean  # keep only the answers this service asks for
    if a.fee_usd > 0 and not a.paid:
        return err("Payment is required before submitting", 402)
    a.due_at = now() + timedelta(days=a.service.sla_days)
    _log(a, "submitted", "Application submitted")
    notify(a.user_id, "Application submitted", f"{a.reference} was received by {a.service.institution.name}.")
    audit("application_submit", a.reference)
    db.session.commit()
    return jsonify(a.to_dict(full=True))


# ---- payments ----
@bp.post("/<int:app_id>/pay")
@auth_required("citizen", "agent")
def pay(app_id):
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    d = request.get_json(silent=True) or {}
    if d.get("method") not in payments.METHODS:
        return err("Unsupported payment method")
    if a.paid or a.fee_usd <= 0:
        return err("Nothing to pay", 409)
    if d["method"] == "agent_cash" and g.user.role != "agent":
        return err("Cash payments are only taken by agents")
    p = payments.initiate(a, d["method"], d.get("msisdn") or a.user.phone)
    audit("payment_initiate", p.txn_ref)
    db.session.commit()
    return jsonify({**p.to_dict(), "message": "Approve the payment prompt on your phone."}), 201


@bp.post("/payments/<txn_ref>/confirm")
@auth_required("citizen", "agent")
def confirm_payment(txn_ref):
    """Mock provider callback. In production this is the provider's signed webhook, not a user call."""
    if current_app.config["PAYMENT_PROVIDER"] != "mock":
        return err("Not available", 404)
    p = Payment.query.filter_by(txn_ref=txn_ref).first()
    if not p or not _own(p.application_id):
        return err("Payment not found", 404)
    if p.status != "pending":
        return err("Payment already processed", 409)
    payments.settle(p, success=(request.get_json(silent=True) or {}).get("success", True))
    if p.status == "success":
        notify(p.application.user_id, "Payment received", f"${p.amount_usd:.2f} for {p.application.reference}")
    audit("payment_" + p.status, p.txn_ref)
    db.session.commit()
    return jsonify(p.to_dict())


# ---- biometrics ----
@bp.post("/<int:app_id>/biometrics")
@auth_required("citizen", "agent")
def capture_biometric(app_id):
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    if a.status != "biometric_pending":
        return err("Biometrics are not requested for this application right now", 409)
    kind = request.form.get("kind", "face")
    if kind == "fingerprint":
        return err("Fingerprint capture needs a certified scanner: please book a service center appointment", 422)
    f = request.files.get("image")
    if kind != "face" or not f:
        return err("A face image is required")
    raw = f.read()
    if len(raw) < 1000:
        return err("Image is too small or unreadable")
    liveness, match = biometrics.verify(kind, raw, request.form.get("liveness", 0))
    name, digest = biometrics.save_image(raw, f"bio{a.id}")
    ok = biometrics.is_verified(kind, liveness, match)
    rec = BiometricRecord(user_id=a.user_id, application_id=a.id, kind=kind,
                          source="agent" if g.user.role == "agent" else "remote",
                                     stored_name=name, sha256=digest, liveness_score=liveness,
                                     match_score=match, status="verified" if ok else "failed")
    db.session.add(rec)
    if ok:
        biometrics.dispatch(rec, a.service.institution)
        _log(a, "in_review", "Remote face verification passed and sent to the institution", "System")
        notify(a.user_id, "Biometrics verified", f"{a.reference} is back with the officer for a decision.")
    audit("biometric_" + rec.status, a.reference)
    db.session.commit()
    return jsonify({"record": rec.to_dict(), "passed": ok,
                    "message": "Verified" if ok else "Verification failed. Retry in good light, facing the camera."}), 201 if ok else 422


# ---- appointments ----
@bp.post("/<int:app_id>/appointments")
@auth_required("citizen", "agent")
def book(app_id):
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    d = request.get_json(silent=True) or {}
    if d.get("center") not in CENTERS:
        return err("Unknown service center")
    try:
        from datetime import datetime
        slot = datetime.fromisoformat(d["slot"]).replace(tzinfo=None)
    except Exception:
        return err("A valid ISO date/time 'slot' is required")
    if slot <= now():
        return err("Choose a future time slot")
    if Appointment.query.filter_by(center=d["center"], slot=slot, status="booked").count() >= 5:
        return err("That slot is full. Please choose another.", 409)
    purpose = d.get("purpose", "biometrics")
    ap = Appointment(application_id=a.id, user_id=a.user_id, center=d["center"], slot=slot, purpose=purpose)
    db.session.add(ap)
    notify(a.user_id, "Appointment booked", f"{d['center']} on {slot:%d %b %Y %H:%M}")
    audit("appointment_book", a.reference)
    db.session.commit()
    return jsonify(ap.to_dict()), 201


@bp.get("/appointments/mine")
@auth_required("citizen")
def my_appointments():
    items = Appointment.query.filter_by(user_id=g.user.id).order_by(Appointment.slot).all()
    return jsonify([x.to_dict() for x in items])


# ---- feedback ----
@bp.post("/<int:app_id>/feedback")
@auth_required("citizen")
def feedback(app_id):
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    if a.status not in ("approved", "ready_for_collection", "completed", "rejected"):
        return err("You can rate the service once a decision has been made", 409)
    d = request.get_json(silent=True) or {}
    try:
        rating = int(d.get("rating"))
    except (TypeError, ValueError):
        rating = 0
    if not 1 <= rating <= 5:
        return err("Rating must be between 1 and 5")
    fb = Feedback.query.filter_by(application_id=a.id).first() or Feedback(application_id=a.id)
    fb.rating, fb.comment = rating, (d.get("comment") or "")[:500]
    db.session.add(fb)
    db.session.commit()
    return jsonify(fb.to_dict()), 201
