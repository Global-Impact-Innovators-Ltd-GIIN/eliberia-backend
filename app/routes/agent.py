"""Agent desk: trained agents (shops, kiosks, county offices) serve citizens who have no smartphone
or internet. An agent can only act for a citizen after the citizen reads out a one-time consent
code sent to the citizen's own phone; consent lasts two hours and every action is audited.
"""
import hmac
import secrets
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.security import generate_password_hash

from ..extensions import db
from ..models import AgentConsent, Application, User, now
from ..utils import audit, auth_required, err, gen_otp, normalize_phone, notify, rate_limit

bp = Blueprint("agent", __name__, url_prefix="/api/agent")

CONSENT_HOURS = 2
COMMISSION_USD = 0.50  # illustrative flat fee per submitted application
MAX_OTP_ATTEMPTS = 5


def active_consent(agent_id, citizen_id):
    return AgentConsent.query.filter(AgentConsent.agent_id == agent_id, AgentConsent.citizen_id == citizen_id,
                                     AgentConsent.expires_at > now()).first()


def _citizen(u):
    return {"id": u.id, "full_name": u.full_name, "phone": u.phone, "nin": u.nin}


@bp.post("/citizens/start")
@auth_required("agent")
@rate_limit("agent_start", 60, 3600)
def start():
    """Find the citizen by phone (or register them) and text them a consent code."""
    d = request.get_json(silent=True) or {}
    phone = normalize_phone(d.get("phone"))
    if not phone:
        return err("Enter a valid Liberian phone number")
    u = User.query.filter_by(phone=phone).first()
    created = False
    if u and u.role != "citizen":
        return err("This number belongs to a staff account", 409)
    if not u:
        name = (d.get("full_name") or "").strip()
        if len(name) < 3:
            return jsonify({"error": "No account with this number yet. Enter the citizen's full name to "
                                     "register them.", "needs_name": True}), 404
        nin = (d.get("nin") or "").strip() or None
        if nin and User.query.filter_by(nin=nin).first():
            return err("This NIN is already registered", 409)
        # unusable random password: the citizen sets their own later via "Forgot password"
        u = User(full_name=name[:150], phone=phone, nin=nin, county=d.get("county"),
                 password_hash=generate_password_hash(secrets.token_urlsafe(32)))
        db.session.add(u)
        db.session.flush()
        created = True
    u.otp_code, u.otp_expires, u.otp_attempts, u.otp_purpose = gen_otp(), now() + timedelta(minutes=10), 0, "consent"
    notify(u.id, "Agent assistance code",
           f"{u.otp_code} is your eLiberia consent code. Give it ONLY to agent {g.user.full_name} if you "
           f"are with them now.", channel="sms")
    audit("agent_consent_requested", phone)
    db.session.commit()
    out = {"citizen": _citizen(u), "created": created,
           "message": "A consent code was sent to the citizen's phone. Ask them to read it to you."}
    if current_app.config["EXPOSE_OTP"]:
        out["dev_otp"] = u.otp_code
    return jsonify(out), 201 if created else 200


@bp.post("/citizens/consent")
@auth_required("agent")
@rate_limit("agent_consent", 60, 600)
def consent():
    d = request.get_json(silent=True) or {}
    u = User.query.filter_by(phone=normalize_phone(d.get("phone")) or "", role="citizen").first()
    if (not u or not u.otp_code or u.otp_purpose != "consent" or not u.otp_expires or u.otp_expires < now()):
        return err("Invalid or expired code", 400)
    if (u.otp_attempts or 0) >= MAX_OTP_ATTEMPTS:
        u.otp_code = None
        db.session.commit()
        return err("Too many wrong codes. Send a new code.", 429)
    if not hmac.compare_digest(u.otp_code, str(d.get("otp") or "")):
        u.otp_attempts = (u.otp_attempts or 0) + 1
        db.session.commit()
        return err("Invalid or expired code", 400)
    u.otp_code, u.otp_attempts, u.phone_verified = None, 0, True  # the code proves they hold the phone
    c = AgentConsent(agent_id=g.user.id, citizen_id=u.id, expires_at=now() + timedelta(hours=CONSENT_HOURS))
    db.session.add(c)
    notify(u.id, "An agent is assisting you",
           f"Agent {g.user.full_name} can act on your behalf for the next {CONSENT_HOURS} hours.")
    audit("agent_consent_granted", u.phone)
    db.session.commit()
    return jsonify({"citizen": _citizen(u), "expires_at": c.expires_at.isoformat()})


@bp.get("/citizens")
@auth_required("agent")
def citizens():
    """Citizens this agent currently has consent to assist."""
    rows = (AgentConsent.query.filter(AgentConsent.agent_id == g.user.id, AgentConsent.expires_at > now())
            .order_by(AgentConsent.id.desc()).all())
    seen, out = set(), []
    for c in rows:
        if c.citizen_id in seen:
            continue
        seen.add(c.citizen_id)
        out.append({**_citizen(db.session.get(User, c.citizen_id)), "expires_at": c.expires_at.isoformat()})
    return jsonify(out)


@bp.get("/applications")
@auth_required("agent")
def applications():
    rows = Application.query.filter_by(agent_id=g.user.id).order_by(Application.id.desc()).limit(100).all()
    return jsonify([a.to_dict() for a in rows])


@bp.get("/stats")
@auth_required("agent")
def stats():
    base = Application.query.filter_by(agent_id=g.user.id)
    submitted = base.filter(Application.status != "draft")
    today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    n = submitted.count()
    return jsonify({
        "assisted_total": n,
        "assisted_today": submitted.filter(Application.created_at >= today).count(),
        "drafts": base.filter(Application.status == "draft").count(),
        "citizens": base.with_entities(Application.user_id).distinct().count(),
        "commission_usd": round(n * COMMISSION_USD, 2),
        "commission_rate_usd": COMMISSION_USD,
    })
