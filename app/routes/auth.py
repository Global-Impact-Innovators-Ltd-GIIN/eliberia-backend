import hmac
from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import User, now
from ..utils import (audit, auth_required, err, gen_otp, make_token, normalize_phone, notify,
                     password_problem, rate_limit)

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
MAX_OTP_ATTEMPTS = 5


def _send_otp(u, purpose="verify"):
    u.otp_code, u.otp_expires, u.otp_attempts = gen_otp(), now() + timedelta(minutes=10), 0
    u.otp_purpose = purpose
    text = {"verify": "Your eLiberia code is {c}",
            "reset": "{c} is your eLiberia password reset code. Never share it with anyone."}[purpose]
    notify(u.id, "eLiberia code", text.format(c=u.otp_code), channel="sms")


def _check_otp(u, code, purpose):
    """Return an error response, or None when the code is right for this purpose."""
    if (not u or not u.otp_code or (u.otp_purpose or "verify") != purpose
            or not u.otp_expires or u.otp_expires < now()):
        return err("Invalid or expired code", 400)
    if (u.otp_attempts or 0) >= MAX_OTP_ATTEMPTS:
        u.otp_code = None
        db.session.commit()
        return err("Too many wrong codes. Request a new code.", 429)
    if not hmac.compare_digest(u.otp_code, str(code or "")):
        u.otp_attempts = (u.otp_attempts or 0) + 1
        db.session.commit()
        return err("Invalid or expired code", 400)
    return None


@bp.post("/register")
@rate_limit("register", 10, 3600)
def register():
    d = request.get_json(silent=True) or {}
    phone = normalize_phone(d.get("phone"))
    name = (d.get("full_name") or "").strip()
    pw = d.get("password") or ""
    if not phone or len(name) < 3:
        return err("Valid full name and phone number are required")
    problem = password_problem(pw)
    if problem:
        return err(problem)
    if User.query.filter_by(phone=phone).first():
        return err("An account with this phone number already exists", 409)
    nin = (d.get("nin") or "").strip() or None
    if nin and User.query.filter_by(nin=nin).first():
        return err("This NIN is already registered", 409)
    u = User(full_name=name[:150], phone=phone, email=d.get("email"), nin=nin, dob=d.get("dob"),
             county=d.get("county"), password_hash=generate_password_hash(pw),
             language=d.get("language", "en"))
    db.session.add(u)
    db.session.flush()
    _send_otp(u)
    audit("register", phone, user_id=u.id)
    db.session.commit()
    out = {"user": u.to_dict(), "message": "Account created. Enter the code sent by SMS."}
    if current_app.config["EXPOSE_OTP"]:
        out["dev_otp"] = u.otp_code
    return jsonify(out), 201


@bp.post("/verify-otp")
@rate_limit("verify", 20, 600)
def verify_otp():
    d = request.get_json(silent=True) or {}
    u = User.query.filter_by(phone=normalize_phone(d.get("phone")) or "").first()
    bad = _check_otp(u, d.get("otp"), "verify")
    if bad:
        return bad
    u.phone_verified, u.otp_code, u.otp_attempts = True, None, 0
    audit("verify_phone", u.phone, user_id=u.id)
    db.session.commit()
    return jsonify({"token": make_token(u), "user": u.to_dict()})


@bp.post("/resend-otp")
@rate_limit("resend", 5, 600)
def resend_otp():
    d = request.get_json(silent=True) or {}
    u = User.query.filter_by(phone=normalize_phone(d.get("phone")) or "").first()
    if u and not u.phone_verified:
        _send_otp(u)
        db.session.commit()
        if current_app.config["EXPOSE_OTP"]:
            return jsonify({"message": "Code sent", "dev_otp": u.otp_code})
    return jsonify({"message": "If the account exists, a code was sent"})


@bp.post("/login")
@rate_limit("login", 20, 600)
def login():
    d = request.get_json(silent=True) or {}
    u = User.query.filter_by(phone=normalize_phone(d.get("phone")) or "").first()
    if u and u.locked_until and u.locked_until > now():
        mins = int((u.locked_until - now()).total_seconds() // 60) + 1
        return err(f"Account temporarily locked after too many failed attempts. Try again in {mins} minutes.", 423)
    if not u or not check_password_hash(u.password_hash, d.get("password") or ""):
        if u:
            u.failed_logins = (u.failed_logins or 0) + 1
            if u.failed_logins >= MAX_FAILED_LOGINS:
                u.locked_until, u.failed_logins = now() + timedelta(minutes=LOCK_MINUTES), 0
                notify(u.id, "Account locked", "Too many failed sign-in attempts. If this was not you, "
                                               "change your password.")
                audit("account_locked", u.phone, user_id=u.id)
            db.session.commit()
        return err("Incorrect phone number or password", 401)
    if not u.phone_verified:
        return err("Phone number not verified", 403)
    u.failed_logins, u.locked_until = 0, None
    audit("login", u.phone, user_id=u.id)
    db.session.commit()
    return jsonify({"token": make_token(u), "user": u.to_dict()})


@bp.get("/me")
@auth_required()
def me():
    return jsonify(g.user.to_dict())


@bp.patch("/me")
@auth_required()
def update_me():
    d = request.get_json(silent=True) or {}
    u = g.user
    for f in ("full_name", "email", "dob", "county", "language"):
        if f in d and d[f] is not None:
            setattr(u, f, str(d[f])[:150])
    if d.get("nin"):
        other = User.query.filter(User.nin == d["nin"], User.id != u.id).first()
        if other:
            return err("This NIN is already registered", 409)
        u.nin = d["nin"]
    audit("profile_update")
    db.session.commit()
    return jsonify(u.to_dict())


@bp.post("/change-password")
@auth_required()
@rate_limit("change_pw", 10, 600)
def change_password():
    d = request.get_json(silent=True) or {}
    u = g.user
    if not check_password_hash(u.password_hash, d.get("current_password") or ""):
        return err("Current password is incorrect", 403)
    problem = password_problem(d.get("new_password") or "")
    if problem:
        return err(problem)
    u.password_hash = generate_password_hash(d["new_password"])
    u.token_version = (u.token_version or 0) + 1  # ends every other session
    notify(u.id, "Password changed", "Your password was changed. All other devices were signed out.")
    audit("password_change")
    db.session.commit()
    return jsonify({"token": make_token(u), "message": "Password changed"})


@bp.post("/logout-all")
@auth_required()
def logout_all():
    g.user.token_version = (g.user.token_version or 0) + 1
    audit("logout_all")
    db.session.commit()
    return jsonify({"message": "Signed out on all devices"})


@bp.get("/activity")
@auth_required()
def activity():
    """Recent security-relevant activity on the signed-in account."""
    from ..models import AuditLog
    rows = AuditLog.query.filter_by(user_id=g.user.id).order_by(AuditLog.id.desc()).limit(15).all()
    return jsonify([{"action": r.action, "ip": r.ip, "created_at": r.created_at.isoformat()} for r in rows])


@bp.post("/forgot")
@rate_limit("forgot", 5, 600)
def forgot():
    """Start a password reset. Also how a citizen registered by an agent sets their first password."""
    d = request.get_json(silent=True) or {}
    u = User.query.filter_by(phone=normalize_phone(d.get("phone")) or "").first()
    out = {"message": "If the account exists, a reset code was sent by SMS."}
    if u:
        _send_otp(u, "reset")
        audit("password_reset_requested", u.phone, user_id=u.id)
        db.session.commit()
        if current_app.config["EXPOSE_OTP"]:
            out["dev_otp"] = u.otp_code
    return jsonify(out)


@bp.post("/reset")
@rate_limit("reset", 20, 600)
def reset():
    d = request.get_json(silent=True) or {}
    problem = password_problem(d.get("new_password") or "")
    if problem:
        return err(problem)
    u = User.query.filter_by(phone=normalize_phone(d.get("phone")) or "").first()
    bad = _check_otp(u, d.get("otp"), "reset")
    if bad:
        return bad
    u.password_hash = generate_password_hash(d["new_password"])
    u.otp_code, u.otp_attempts, u.phone_verified = None, 0, True
    u.failed_logins, u.locked_until = 0, None
    u.token_version = (u.token_version or 0) + 1
    notify(u.id, "Password reset", "Your password was reset. All devices were signed out.")
    audit("password_reset", u.phone, user_id=u.id)
    db.session.commit()
    return jsonify({"token": make_token(u), "user": u.to_dict()})
