from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import func
from werkzeug.security import generate_password_hash

from ..extensions import db
from ..models import (Appointment, Application, ApplicationEvent, AuditLog, BiometricRecord, Institution,
                      Notification, Service, User, now)
from ..rules import normalize_workflow
from ..services import biometrics, certificates
from ..utils import (ADMIN_PERMS, audit, auth_required, err, has_perm, need, normalize_phone, notify,
                     password_problem, record_access)

bp = Blueprint("staff", __name__, url_prefix="/api")

REVIEWERS = ("officer", "inst_admin", "admin")
INSTITUTION_ROLES = ("officer", "inst_admin")


def _scope(q):
    """Institution staff only see their own institution's applications; platform admins see all."""
    if g.user.role in INSTITUTION_ROLES:
        q = q.join(Service).filter(Service.institution_id == g.user.institution_id)
    return q


def _support_gate():
    """Platform admins need the 'support' permission to open citizens' applications."""
    if g.user.role == "admin" and not has_perm(g.user, "support"):
        return err("Your admin account does not have the 'support' permission", 403)
    return None


def _stage_gate(stage, index, actors):
    """Why the signed-in person may not complete this stage, or None."""
    if stage["requires_admin"] and g.user.role == "officer":
        return f"The '{stage['name']}' stage must be completed by an institution administrator."
    if stage["separate_officer"] and index > 0 and len(actors) >= index and actors[index - 1] == g.user.id:
        return (f"The '{stage['name']}' stage must be completed by a different officer from the one who "
                f"completed the previous stage.")
    return None


def _get(app_id):
    return _scope(Application.query).filter(Application.id == app_id).first()


# ---- notifications (any user) ----
@bp.get("/notifications")
@auth_required()
def notifications():
    items = (Notification.query.filter_by(user_id=g.user.id, channel="app")
             .order_by(Notification.id.desc()).limit(50).all())
    return jsonify([n.to_dict() for n in items])


@bp.post("/notifications/read")
@auth_required()
def mark_read():
    Notification.query.filter_by(user_id=g.user.id, read=False).update({"read": True})
    db.session.commit()
    return jsonify({"ok": True})


# ---- review queue ----
@bp.get("/staff/applications")
@auth_required(*REVIEWERS)
def queue():
    gate = _support_gate()
    if gate:
        return gate
    q = _scope(Application.query)
    if request.args.get("status"):
        q = q.filter(Application.status == request.args["status"])
    else:
        q = q.filter(Application.status != "draft")
    return jsonify([a.to_dict() for a in q.order_by(Application.due_at).limit(200)])


@bp.get("/staff/applications/<int:app_id>")
@auth_required(*REVIEWERS)
def staff_detail(app_id):
    gate = _support_gate()
    if gate:
        return gate
    a = _get(app_id)
    if not a:
        return err("Application not found", 404)
    record_access(a, "Opened the application")
    db.session.commit()
    return jsonify(a.to_dict(full=True))


@bp.post("/staff/applications/<int:app_id>/action")
@auth_required(*REVIEWERS)
def action(app_id):
    gate = _support_gate()
    if gate:
        return gate
    a = _get(app_id)
    if not a:
        return err("Application not found", 404)
    d = request.get_json(silent=True) or {}
    act, note = d.get("action"), (d.get("note") or "").strip()
    svc = a.service
    wf = normalize_workflow(svc.workflow)
    i = min(a.stage_index or 0, len(wf) - 1)
    actors = list(a.stage_actors or [])
    if act == "start_review" and a.status == "submitted":
        a.assigned_to, a.stage_index, a.stage_actors = g.user.id, 0, []
        status, msg = "in_review", ("Officer started reviewing" if len(wf) == 1
                                    else f"Review started at stage 1 of {len(wf)}: {wf[0]['name']}")
    elif act == "advance" and a.status == "in_review":
        if i >= len(wf) - 1:
            return err("This is the final stage. Approve or reject the application.", 409)
        gate = _stage_gate(wf[i], i, actors)
        if gate:
            return err(gate, 409)
        a.stage_actors, a.stage_index, a.assigned_to = actors[:i] + [g.user.id], i + 1, None
        status = "in_review"
        msg = (f"Stage '{wf[i]['name']}' completed" + (f" - {note}" if note else "")
               + f". Now at stage {i + 2} of {len(wf)}: {wf[i + 1]['name']}.")
    elif act == "send_back" and a.status == "in_review":
        if i == 0:
            return err("This is the first stage. Ask the applicant for more information instead.", 409)
        if not note:
            return err("Explain why you are sending it back")
        a.stage_actors, a.stage_index, a.assigned_to = actors[:i - 1], i - 1, None
        status, msg = "in_review", f"Sent back to stage '{wf[i - 1]['name']}': {note}"
    elif act == "request_info" and a.status in ("in_review", "submitted"):
        if not note:
            return err("Tell the applicant what is missing")
        status, msg = "info_requested", note
    elif act == "request_biometrics" and a.status == "in_review":
        status, msg = "biometric_pending", note or "Please complete identity verification"
    elif act == "approve" and a.status == "in_review":
        if i < len(wf) - 1:
            return err(f"Complete the '{wf[i]['name']}' stage first. Approval happens at the final stage, "
                       f"'{wf[-1]['name']}'.", 409)
        gate = _stage_gate(wf[i], i, actors)
        if gate:
            return err(gate, 409)
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
@auth_required(*REVIEWERS)
def attend(ap_id):
    """Center officer records an in-person biometric capture (fingerprint scanner) completed."""
    ap = db.session.get(Appointment, ap_id)
    a = _get(ap.application_id) if ap else None
    if not ap or not a:
        return err("Appointment not found", 404)
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


# ---- dashboards ----
@bp.get("/admin/stats")
@auth_required(*REVIEWERS)
def stats():
    base = _scope(Application.query)
    by_status = dict(base.with_entities(Application.status, func.count(Application.id))
                     .group_by(Application.status).all())
    overdue = base.filter(Application.due_at < now(),
                          Application.status.in_(["submitted", "in_review"])).count()
    out = {"by_status": by_status, "total": sum(by_status.values()), "overdue": overdue}
    by_service = (base.with_entities(Application.service_id, func.count(Application.id))
                  .filter(Application.status != "draft").group_by(Application.service_id).all())
    names = {s.id: s.name for s in Service.query.filter(Service.id.in_([i for i, _ in by_service] or [0]))}
    out["by_service"] = sorted(({"name": names.get(i, "?"), "count": n} for i, n in by_service),
                               key=lambda x: -x["count"])[:6]
    if g.user.role == "admin":
        out["users"] = User.query.filter_by(role="citizen").count()
        out["institutions"] = Institution.query.count()
        if has_perm(g.user, "finance"):
            out["revenue_usd"] = round(sum(a.fee_usd for a in Application.query.filter_by(paid=True)), 2)
    elif g.user.role == "inst_admin":
        ids = [s.id for s in Service.query.filter_by(institution_id=g.user.institution_id)]
        paid = Application.query.filter(Application.paid.is_(True), Application.service_id.in_(ids or [0]))
        out["revenue_usd"] = round(sum(a.fee_usd for a in paid), 2)
    return jsonify(out)


@bp.get("/admin/audit")
@auth_required("admin")
def audit_log():
    return need("audit") or jsonify([x.to_dict() for x in AuditLog.query.order_by(AuditLog.id.desc()).limit(200)])


# ---- platform team ----
@bp.post("/admin/officers")
@auth_required("admin")
def create_officer():
    """Create an agent, an institution officer, an institution administrator or a platform admin."""
    gate = need("team")
    if gate:
        return gate
    d = request.get_json(silent=True) or {}
    phone = normalize_phone(d.get("phone"))
    role = d.get("role", "officer")
    if role not in ("officer", "agent", "inst_admin", "admin"):
        return err("role must be officer, agent, inst_admin or admin")
    problem = password_problem(d.get("password") or "")
    if not phone or len((d.get("full_name") or "").strip()) < 3 or problem:
        return err(problem or "Full name and a valid phone number are required")
    inst = None
    if role in INSTITUTION_ROLES:
        inst = db.session.get(Institution, d.get("institution_id") or 0)
        if not inst:
            return err("Choose the institution")
    perms = None
    if role == "admin":
        if not has_perm(g.user, "all"):
            return err("Only a super administrator can add platform administrators", 403)
        perms = [p for p in (d.get("perms") or []) if p in ADMIN_PERMS]
        if not perms:
            return err("Choose at least one permission for this administrator")
    if User.query.filter_by(phone=phone).first():
        return err("Phone already registered", 409)
    u = User(full_name=d["full_name"].strip(), phone=phone, role=role, admin_perms=perms,
             institution_id=inst.id if inst else None,
             password_hash=generate_password_hash(d["password"]), phone_verified=True,
             must_change_password=current_app.config["FORCE_PASSWORD_CHANGE"])
    db.session.add(u)
    audit(f"admin_create_{role}", phone)
    db.session.commit()
    return jsonify(u.to_dict()), 201
