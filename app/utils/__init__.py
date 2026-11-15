import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import current_app, g, jsonify, request

from ..extensions import db
from ..models import AuditLog, Notification, User


def err(message, code=400):
    return jsonify({"error": message}), code


def normalize_phone(phone):
    """Liberia: +231 country code. Accept 0770000000, 770000000, +231770000000."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("231"):
        digits = digits[3:]
    digits = digits.lstrip("0")
    if not 7 <= len(digits) <= 9:
        return None
    return "+231" + digits


def make_token(user):
    exp = datetime.now(timezone.utc) + timedelta(hours=current_app.config["TOKEN_HOURS"])
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": exp},
                      current_app.config["SECRET_KEY"], algorithm="HS256")


def auth_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return err("Authentication required", 401)
            try:
                payload = jwt.decode(header[7:], current_app.config["SECRET_KEY"], algorithms=["HS256"])
            except jwt.PyJWTError:
                return err("Invalid or expired token", 401)
            user = db.session.get(User, int(payload["sub"]))
            if not user:
                return err("User not found", 401)
            if roles and user.role not in roles:
                return err("You do not have permission for this action", 403)
            g.user = user
            return fn(*a, **kw)
        return wrapper
    return deco


def audit(action, detail="", user_id=None):
    uid = user_id or (g.user.id if getattr(g, "user", None) else None)
    db.session.add(AuditLog(user_id=uid, action=action, detail=detail[:300], ip=request.remote_addr))


def notify(user_id, title, body, channel="app"):
    """In-app notification. SMS/email channels are plugged in via services.messaging (stub logs only)."""
    db.session.add(Notification(user_id=user_id, title=title, body=body, channel=channel))


def gen_otp():
    return f"{secrets.randbelow(10**6):06d}"


def gen_reference(prefix="LR"):
    return f"{prefix}-{datetime.now(timezone.utc):%y%m}-{secrets.token_hex(3).upper()}"
