"""Institution portal, platform (super admin) console, and citizen data-control endpoints."""
import re

from flask import Blueprint, g, jsonify, request
from werkzeug.security import generate_password_hash

from ..extensions import db
from ..models import AccessLog, Application, Certificate, Institution, Service, User
from ..services import forms, identity
from ..utils import audit, auth_required, err, has_perm, need, normalize_phone, password_problem
from .applications import _own
from .extras import _admin_dict, _apply_service

bp = Blueprint("org", __name__, url_prefix="/api")


@bp.get("/meta/identity")
def identity_meta():
    return jsonify(identity.catalog())


# ======================= institution portal =======================
def _my_institution():
    return db.session.get(Institution, g.user.institution_id or 0)


def _apply_institution(inst, d):
    for key, limit in (("description", 500), ("guidelines", 2000), ("contact_phone", 30), ("contact_email", 120)):
        if key in d:
            setattr(inst, key, str(d[key] or "").strip()[:limit])
    if "name" in d and len(str(d["name"]).strip()) >= 3:
        inst.name = str(d["name"]).strip()[:200]
    if inst.contact_email and not forms.EMAIL.fullmatch(inst.contact_email):
        return "Contact email is not a valid email address"
    return None


@bp.get("/institution/profile")
@auth_required("inst_admin", "officer")
def institution_profile():
    inst = _my_institution()
    return jsonify(inst.to_dict()) if inst else err("No institution is linked to this account", 404)


@bp.put("/institution/profile")
@auth_required("inst_admin")
def update_institution_profile():
    inst = _my_institution()
    if not inst:
        return err("No institution is linked to this account", 404)
    d = request.get_json(silent=True) or {}
    d.pop("name", None)  # the official name is managed by the platform
    problem = _apply_institution(inst, d)
    if problem:
        db.session.rollback()
        return err(problem)
    audit("institution_profile_updated", inst.code)
    db.session.commit()
    return jsonify(inst.to_dict())


@bp.get("/institution/services")
@auth_required("inst_admin")
def institution_services():
    rows = Service.query.filter_by(institution_id=g.user.institution_id).order_by(Service.category, Service.name)
    return jsonify([_admin_dict(s) for s in rows])


@bp.post("/institution/services")
@auth_required("inst_admin")
def institution_create_service():
    d = request.get_json(silent=True) or {}
    code = re.sub(r"[^A-Z0-9_]", "", str(d.get("code") or "").upper().replace(" ", "_"))[:40]
    if len(code) < 3:
        return err("Give the service a code of at least 3 letters, e.g. FISHING_LICENSE")
    if Service.query.filter_by(code=code).first():
        return err("A service with this code already exists", 409)
    s = Service(code=code, name="", category="", institution_id=g.user.institution_id)
    problem = _apply_service(s, d, creating=True, lock_institution=g.user.institution_id)
    if problem:
        return err(problem)
    db.session.add(s)
    audit("service_created", code)
    db.session.commit()
    return jsonify(_admin_dict(s)), 201


@bp.put("/institution/services/<code>")
@auth_required("inst_admin")
def institution_update_service(code):
    s = Service.query.filter_by(code=code, institution_id=g.user.institution_id).first()
    if not s:
        return err("Service not found", 404)
    problem = _apply_service(s, request.get_json(silent=True) or {}, creating=False,
                             lock_institution=g.user.institution_id)
    if problem:
        db.session.rollback()
        return err(problem)
    audit("service_updated", code)
    db.session.commit()
    return jsonify(_admin_dict(s))


@bp.get("/institution/team")
@auth_required("inst_admin")
def institution_team():
    rows = (User.query.filter(User.institution_id == g.user.institution_id, User.role.in_(("officer", "inst_admin")))
            .order_by(User.full_name).all())
    return jsonify([u.to_dict() for u in rows])


@bp.post("/institution/team")
@auth_required("inst_admin")
def institution_add_officer():
    d = request.get_json(silent=True) or {}
    phone = normalize_phone(d.get("phone"))
    problem = password_problem(d.get("password") or "")
    if not phone or len((d.get("full_name") or "").strip()) < 3 or problem:
        return err(problem or "Full name and a valid phone number are required")
    if User.query.filter_by(phone=phone).first():
        return err("Phone already registered", 409)
    u = User(full_name=d["full_name"].strip(), phone=phone, role="officer", institution_id=g.user.institution_id,
             password_hash=generate_password_hash(d["password"]), phone_verified=True)
    db.session.add(u)
    audit("institution_create_officer", phone)
    db.session.commit()
    return jsonify(u.to_dict()), 201


# ======================= platform console =======================
@bp.get("/admin/institutions")
@auth_required("admin")
def admin_institutions():
    out = []
    for i in Institution.query.order_by(Institution.name):
        out.append({**i.to_dict(), "services": Service.query.filter_by(institution_id=i.id).count(),
                    "staff": User.query.filter(User.institution_id == i.id).count()})
    return jsonify(out)


@bp.post("/admin/institutions")
@auth_required("admin")
def admin_create_institution():
    gate = need("institutions")
    if gate:
        return gate
    d = request.get_json(silent=True) or {}
    code = re.sub(r"[^A-Z0-9]", "", str(d.get("code") or "").upper())[:20]
    name = str(d.get("name") or "").strip()
    if len(code) < 2 or len(name) < 3:
        return err("A short code (e.g. MOA) and the full name are required")
    if Institution.query.filter_by(code=code).first():
        return err("An institution with this code already exists", 409)
    inst = Institution(code=code, name=name[:200])
    problem = _apply_institution(inst, d)
    if problem:
        return err(problem)
    db.session.add(inst)
    audit("institution_created", code)
    db.session.commit()
    return jsonify(inst.to_dict()), 201


@bp.put("/admin/institutions/<int:inst_id>")
@auth_required("admin")
def admin_update_institution(inst_id):
    gate = need("institutions")
    if gate:
        return gate
    inst = db.session.get(Institution, inst_id)
    if not inst:
        return err("Institution not found", 404)
    problem = _apply_institution(inst, request.get_json(silent=True) or {})
    if problem:
        db.session.rollback()
        return err(problem)
    audit("institution_updated", inst.code)
    db.session.commit()
    return jsonify(inst.to_dict())


@bp.get("/admin/team")
@auth_required("admin")
def admin_team():
    gate = need("team")
    if gate:
        return gate
    rows = User.query.filter(User.role != "citizen").order_by(User.role, User.full_name).all()
    insts = {i.id: i.name for i in Institution.query}
    return jsonify([{**u.to_dict(), "institution": insts.get(u.institution_id)} for u in rows])


# ======================= citizen data control =======================
@bp.get("/applications/<int:app_id>/prefill")
@auth_required("citizen", "agent")
def prefill(app_id):
    """Answers we already hold for this person, so they never type the same thing twice."""
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    if a.beneficiary:  # applying for someone else: their details are not the account holder's
        return jsonify({"values": {}})
    u = a.user
    profile = {"full_name": u.full_name, "dob": u.dob, "county": u.county, "email": u.email, "phone": u.phone}
    previous = {}
    past = (Application.query.filter(Application.user_id == u.id, Application.id != a.id,
                                     Application.status != "draft").order_by(Application.id.desc()).limit(10))
    for p in past:
        if p.beneficiary:
            continue
        for k, v in (p.data or {}).items():
            previous.setdefault(k, v)
    values = {}
    for f in a.service.form_fields or []:
        name = f["name"]
        if f.get("type") == "identity":
            options = []
            if previous.get(name):
                options.append((previous.get(name + "__country"), previous.get(name + "__doc"), previous[name]))
            if u.id_number:
                options.append((u.nationality, u.id_doc_type, u.id_number))
            if u.nin:
                options.append((identity.HOME, identity.NATIONAL_ID, u.nin))
            for country, doc, number in options:
                trial = {name: number, name + "__country": country, name + "__doc": doc}
                probe = type("S", (), {"form_fields": [{**f, "required": True}]})
                clean, problems = forms.validate(probe, trial)
                if not problems:
                    values.update(clean)
                    break
            continue
        v = previous.get(name) or profile.get(name)
        if not v:
            continue
        if f.get("type") == "select" and v not in (f.get("options") or []):
            continue
        values[name] = str(v)
    return jsonify({"values": values})


@bp.get("/applications/<int:app_id>/access")
@auth_required("citizen")
def access_history(app_id):
    """Transparency: every time a member of staff opened this application or one of its documents."""
    a = _own(app_id)
    if not a:
        return err("Application not found", 404)
    rows = AccessLog.query.filter_by(application_id=a.id).order_by(AccessLog.id.desc()).limit(50).all()
    return jsonify([r.to_dict() for r in rows])


@bp.get("/me/export")
@auth_required("citizen")
def export_my_data():
    """Everything eLiberia holds about the signed-in citizen, in one file."""
    u = g.user
    apps = Application.query.filter_by(user_id=u.id).order_by(Application.id).all()
    ids = [a.id for a in apps] or [0]
    audit("data_export")
    db.session.commit()
    return jsonify({
        "profile": u.to_dict(),
        "applications": [{**a.to_dict(), "answers": a.data, "documents": [d.to_dict() for d in a.documents],
                          "history": [e.to_dict() for e in a.events]} for a in apps],
        "certificates": [c.to_dict() for c in Certificate.query.filter(Certificate.application_id.in_(ids))],
        "staff_access": [r.to_dict() for r in AccessLog.query.filter(AccessLog.application_id.in_(ids))],
    })
