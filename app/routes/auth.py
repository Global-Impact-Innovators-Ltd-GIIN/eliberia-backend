from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import User, now
from ..utils import audit, auth_required, err, gen_otp, make_token, normalize_phone, notify

bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@bp.post("/register")
def register():
    d = request.get_json(silent=True) or {}
    phone = normalize_phone(d.get("phone"))
    name = (d.get("full_name") or "").strip()
    pw = d.get("password") or ""
    if not phone or not name:
        return err("Valid full name and phone number are required")
    if len(pw) < 8:
        return err("Password must be at least 8 characters")
    if User.query.filter_by(phone=phone).first():
        return err("An account with this phone number already exists", 409)
    nin = (d.get("nin") or "").strip() or None
    if nin and User.query.filter_by(nin=nin).first():
        return err("This NIN is already registered", 409)
    u = User(full_name=name, phone=phone, email=d.get("email"), nin=nin, dob=d.get("dob"),
             county=d.get("county"), password_hash=generate_password_hash(pw),
             language=d.get("language", "en"))
    u.otp_code, u.otp_expires = gen_otp(), now() + timedelta(minutes=10)
    db.session.add(u)
    db.session.flush()
    audit("register", phone, user_id=u.id)
    notify(u.id, "Verify your phone", f"Your eLiberia code is {u.otp_code}", channel="sms")
    db.session.commit()
    out = {"user": u.to_dict(), "message": "Account created. Enter the OTP sent by SMS."}
    if current_app.config["EXPOSE_OTP"]:
        out["dev_otp"] = u.otp_code
    return jsonify(out), 201


@bp.post("/verify-otp")
def verify_otp():
    d = request.get_json(silent=True) or {}
    u = User.query.filter_by(phone=normalize_phone(d.get("phone")) or "").first()
    if not u or not u.otp_code or u.otp_code != str(d.get("otp")) or u.otp_expires < now():
        return err("Invalid or expired code", 400)
    u.phone_verified, u.otp_code = True, None
    audit("verify_phone", u.phone, user_id=u.id)
    db.session.commit()
    return jsonify({"token": make_token(u), "user": u.to_dict()})


@bp.post("/resend-otp")
def resend_otp():
    d = request.get_json(silent=True) or {}
    u = User.query.filter_by(phone=normalize_phone(d.get("phone")) or "").first()
    if u and not u.phone_verified:
        u.otp_code, u.otp_expires = gen_otp(), now() + timedelta(minutes=10)
        notify(u.id, "Verify your phone", f"Your eLiberia code is {u.otp_code}", channel="sms")
        db.session.commit()
        if current_app.config["EXPOSE_OTP"]:
            return jsonify({"message": "Code sent", "dev_otp": u.otp_code})
    return jsonify({"message": "If the account exists, a code was sent"})


@bp.post("/login")
def login():
    d = request.get_json(silent=True) or {}
    u = User.query.filter_by(phone=normalize_phone(d.get("phone")) or "").first()
    if not u or not check_password_hash(u.password_hash, d.get("password") or ""):
        return err("Incorrect phone number or password", 401)
    if not u.phone_verified:
        return err("Phone number not verified", 403)
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
            setattr(u, f, d[f])
    if d.get("nin"):
        other = User.query.filter(User.nin == d["nin"], User.id != u.id).first()
        if other:
            return err("This NIN is already registered", 409)
        u.nin = d["nin"]
    db.session.commit()
    return jsonify(u.to_dict())
