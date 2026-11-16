from flask import Blueprint, g, jsonify, request
from sqlalchemy import func

from ..extensions import db
from ..models import (Appointment, Application, ApplicationEvent, AuditLog, BiometricRecord,
                      Notification, Service, User, now)
from ..services import biometrics, certificates
from ..utils import audit, auth_required, err, notify

bp = Blueprint("staff", __name__, url_prefix="/api")


def _scope(q):
    """Officers only see applications for their own institution; admins see everything."""
    if g.user.role == "officer":
        q = q.join(Service).filter(Service.institution_id == g.user.institution_id)
    return q


def _get(app_id):
    a = _scope(Application.query).filter(Application.id == app_id).first()
    return a


# ---- notifications (any user) ----
@bp.get("/notifications")
@auth_required()
def notifications():
    items = Notification.query.filter_by(user_id=g.user.id, channel="app").order_by(Notification.id.desc()).limit(50).all()
    return jsonify([n.to_dict() for n in items])


@bp.post("/notifications/read")
@auth_required()
def mark_read():
    Notification.query.filter_by(user_id=g.user.id, read=False).update({"read": True})
    db.session.commit()
    return jsonify({"ok": True})


# ---- officer queue ----
@bp.get("/staff/applications")
@auth_required("officer", "admin")
def queue():
    q = _scope(Application.query)
    if request.args.get("status"):
        q = q.filter(Application.status == request.args["status"])
    else:
        q = q.filter(Application.status != "draft")
    return jsonify([a.to_dict() for a in q.order_by(Application.due_at).limit(200)])


@bp.get("/staff/applications/<int:app_id>")
@auth_required("officer", "admin")
def staff_detail(app_id):
    a = _get(app_id)
    if not a:
        return err("Application not found", 404)
    return jsonify(a.to_dict(full=True))


@bp.post("/staff/applications/<int:app_id>/action")
@auth_required("officer", "admin")
def action(app_id):
    a = _get(app_id)
    if not a:
        return err("Application not found", 404)
    d = request.get_json(silent=True) or {}
    act, note = d.get("action"), (d.get("note") or "").strip()
    svc = a.service
    if act == "start_review" and a.status == "submitted":
        a.assigned_to = g.user.id
        status, msg = "in_review", "Officer started reviewing"
    elif act == "request_info" and a.status in ("in_review", "submitted"):
        if not note:
            return err("Tell the applicant what is missing")
        status, msg = "info_requested", note
    elif act == "request_biometrics" and a.status == "in_review":
        status, msg = "biometric_pending", note or "Please complete identity verification"
    elif act == "approve" and a.status == "in_review":
        if svc.requires_face and not any(b.status == "dispatched" and b.kind == "face" for b in
                                        BiometricRecord.query.filter_by(application_id=a.id)):
            return err("Face verification is required before approval. Request biometrics first.", 409)
        cert = certificates.issue(a)
        status, msg = "approved", (note or "Application approved") + f". Certificate {cert.number} issued."
    elif act == "reject" and a.status in ("in_review", "submitted"):
        if not note:
            return err("A reason is required to reject")
        a.decision_note = note
        status, msg = "rejected", note
    elif act == "ready" and a.status == "approved":
        status, msg = "ready_for_collection", note or "Ready for delivery / collection"
    elif act == "complete" and a.status == "ready_for_collection":
        status, msg = "completed", "Delivered to applicant"
    else:
        return err(f"Action '{act}' is not allowed while status is '{a.status}'", 409)
    a.status = status
    db.session.add(ApplicationEvent(application=a, status=status, message=msg, actor=g.user.full_name))
    notify(a.user_id, f"Update on {a.reference}", f"Status: {status.replace('_', ' ')}. {msg}")
    audit("staff_" + act, a.reference)
    db.session.commit()
    return jsonify(a.to_dict(full=True))


@bp.post("/staff/appointments/<int:ap_id>/attend")
@auth_required("officer", "admin")
def attend(ap_id):
    """Center officer records an in-person biometric capture (fingerprint scanner) completed."""
    ap = db.session.get(Appointment, ap_id)
    if not ap:
        return err("Appointment not found", 404)
    a = db.session.get(Application, ap.application_id)
    ap.status = "attended"
    rec = BiometricRecord(user_id=ap.user_id, application_id=a.id, kind="fingerprint", source="center",
                          sha256="center-" + str(ap.id), liveness_score=1.0, match_score=1.0, status="verified")
    db.session.add(rec)
    biometrics.dispatch(rec, a.service.institution)
    if a.status == "biometric_pending":
        a.status = "in_review"
        db.session.add(ApplicationEvent(application=a, status="in_review", actor=g.user.full_name,
                                        message="In-person biometrics captured at service center"))
    audit("appointment_attend", a.reference)
    db.session.commit()
    return jsonify(ap.to_dict())


# ---- admin ----
@bp.get("/admin/stats")
@auth_required("officer", "admin")
def stats():
    base = _scope(Application.query)
    by_status = dict(base.with_entities(Application.status, func.count(Application.id))
                     .group_by(Application.status).all())
    overdue = base.filter(Application.due_at < now(),
                          Application.status.in_(["submitted", "in_review"])).count()
    out = {"by_status": by_status, "total": sum(by_status.values()), "overdue": overdue}
    if g.user.role == "admin":
        out["users"] = User.query.filter_by(role="citizen").count()
        paid = Application.query.filter_by(paid=True)
        out["revenue_usd"] = round(sum(a.fee_usd for a in paid), 2)
    return jsonify(out)


@bp.get("/admin/audit")
@auth_required("admin")
def audit_log():
    return jsonify([x.to_dict() for x in AuditLog.query.order_by(AuditLog.id.desc()).limit(200)])


@bp.post("/admin/officers")
@auth_required("admin")
def create_officer():
    from werkzeug.security import generate_password_hash
    from ..utils import normalize_phone
    d = request.get_json(silent=True) or {}
    phone = normalize_phone(d.get("phone"))
    from ..utils import password_problem
    role = d.get("role", "officer")
    if role not in ("officer", "agent"):
        return err("role must be officer or agent")
    problem = password_problem(d.get("password") or "")
    if not phone or len((d.get("full_name") or "").strip()) < 3 or problem:
        return err(problem or "Full name and a valid phone number are required")
    if role == "officer" and not d.get("institution_id"):
        return err("Choose the officer's institution")
    if User.query.filter_by(phone=phone).first():
        return err("Phone already registered", 409)
    u = User(full_name=d["full_name"].strip(), phone=phone, role=role,
             institution_id=d.get("institution_id") if role == "officer" else None,
             password_hash=generate_password_hash(d["password"]), phone_verified=True)
    db.session.add(u)
    audit(f"admin_create_{role}", phone)
    db.session.commit()
    return jsonify(u.to_dict()), 201
