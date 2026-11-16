import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from functools import wraps

import jwt
from flask import current_app, g, jsonify, request

from ..extensions import db
from ..models import AccessLog, AuditLog, Notification, User


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


def password_problem(pw):
    """Return a human message if the password is too weak, else None."""
    if len(pw or "") < 8:
        return "Password must be at least 8 characters"
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        return "Password must contain both letters and numbers"
    return None


def client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "unknown"


_hits = defaultdict(deque)


def rate_limit(name, limit, window_seconds):
    """Small in-memory sliding-window limiter per client IP (single-worker deployments).
    For multi-instance production, back this with Redis."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if current_app.config.get("TESTING") and not current_app.config.get("RATE_LIMIT_IN_TESTS"):
                return fn(*a, **kw)
            q = _hits[f"{name}:{client_ip()}"]
            t = time.time()
            while q and q[0] <= t - window_seconds:
                q.popleft()
            if len(q) >= limit:
                resp = jsonify({"error": "Too many attempts. Please wait a few minutes and try again."})
                resp.headers["Retry-After"] = str(int(window_seconds - (t - q[0])) + 1)
                return resp, 429
            q.append(t)
            return fn(*a, **kw)
        return wrapper
    return deco


def make_token(user):
    exp = datetime.now(timezone.utc) + timedelta(hours=current_app.config["TOKEN_HOURS"])
    return jwt.encode({"sub": str(user.id), "role": user.role, "v": user.token_version or 0, "exp": exp},
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
            # tokens are revoked when the user changes password or signs out everywhere
            if payload.get("v", 0) != (user.token_version or 0):
                return err("Session ended. Please sign in again.", 401)
            if user.disabled:
                return err("This account has been disabled", 403)
            if user.must_change_password and request.endpoint not in PASSWORD_CHANGE_ENDPOINTS:
                return jsonify({"error": "Choose a new password before continuing - yours was set by someone else.",
                                "must_change_password": True}), 403
            if roles and user.role not in roles:
                return err("You do not have permission for this action", 403)
            g.user = user
            return fn(*a, **kw)
        return wrapper
    return deco


def audit(action, detail="", user_id=None):
    uid = user_id or (g.user.id if getattr(g, "user", None) else None)
    db.session.add(AuditLog(user_id=uid, action=action, detail=detail[:300], ip=client_ip()))


PASSWORD_CHANGE_ENDPOINTS = ("auth.me", "auth.change_password", "auth.logout_all")


def notify(user_id, title, body, channel="app"):
    """Record a notification and text it: one-time codes always, status updates when SMS_UPDATES is on."""
    from ..services import sms
    db.session.add(Notification(user_id=user_id, title=title, body=body, channel=channel))
    if channel == "sms" or current_app.config.get("SMS_UPDATES"):
        u = db.session.get(User, user_id)
        if u and not u.disabled:
            sms.queue(u.phone, body if channel == "sms" else f"eLiberia: {title}. {body}")


def gen_otp():
    return f"{secrets.randbelow(10**6):06d}"


def gen_reference(prefix="LR"):
    return f"{prefix}-{datetime.now(timezone.utc):%y%m}-{secrets.token_hex(3).upper()}"


def gen_bill_id():
    return "88" + "".join(str(secrets.randbelow(10)) for _ in range(10))


# areas a platform administrator can be limited to; None / ["all"] means super administrator
ADMIN_PERMS = ("services", "team", "institutions", "audit", "support", "finance")


def has_perm(user, perm):
    if user.role != "admin":
        return False
    perms = user.admin_perms
    return not perms or "all" in perms or perm in perms


def need(perm):
    """Error response when the signed-in admin lacks a permission, else None."""
    if has_perm(g.user, perm):
        return None
    return err(f"Your admin account does not have the '{perm}' permission", 403)


def record_access(application, what):
    """Note that a member of staff looked at a citizen's application (at most once per half hour per action)."""
    from datetime import timedelta as _td
    from ..models import Institution, now
    u = g.user
    recent = AccessLog.query.filter(AccessLog.application_id == application.id, AccessLog.viewer_id == u.id,
                                    AccessLog.what == what, AccessLog.created_at > now() - _td(minutes=30)).first()
    if recent:
        return
    inst = db.session.get(Institution, u.institution_id) if u.institution_id else None
    db.session.add(AccessLog(application_id=application.id, viewer_id=u.id, viewer_name=u.full_name,
                             viewer_role=u.role, organisation=inst.name if inst else "eLiberia platform team",
                             what=what[:120]))
