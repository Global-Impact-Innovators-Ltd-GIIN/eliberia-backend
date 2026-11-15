import re

from flask import Blueprint, jsonify, request
from sqlalchemy import func

from ..models import Application, Certificate, Feedback, Institution, Service
from ..services import certificates
from ..utils import err, rate_limit

bp = Blueprint("public", __name__, url_prefix="/api")


@bp.get("/verify/<number>")
@rate_limit("verify_cert", 60, 600)
def verify_certificate(number):
    """Public authenticity check for an e-certificate (what the QR code points to)."""
    cert = Certificate.query.filter_by(number=number.strip().upper()).first()
    if not cert:
        return jsonify({"valid": False, "reason": "No certificate exists with this number."}), 404
    if not certificates.is_authentic(cert):
        return jsonify({"valid": False, "reason": "This certificate failed its signature check."}), 200
    if cert.revoked:
        return jsonify({"valid": False, "reason": "This certificate has been revoked.", **cert.to_dict()})
    return jsonify({"valid": True, **cert.to_dict()})


@bp.get("/public/stats")
def public_stats():
    avg = Feedback.query.with_entities(func.avg(Feedback.rating)).scalar()
    return jsonify({
        "services": Service.query.filter_by(active=True).count(),
        "institutions": Institution.query.count(),
        "applications": Application.query.filter(Application.status != "draft").count(),
        "certificates": Certificate.query.count(),
        "rating": round(float(avg), 1) if avg else None,
    })


@bp.get("/services/popular")
def popular():
    rows = (Service.query.outerjoin(Application, Application.service_id == Service.id)
            .filter(Service.active.is_(True)).group_by(Service.id)
            .order_by(func.count(Application.id).desc(), Service.fee_usd.desc()).limit(6).all())
    return jsonify([s.to_dict() for s in rows])


STOP = set("a an the i my me to for of how do can get need want is are what where when in on and or "
           "please apply application new with it be".split())
TOPICS = [
    (("track", "status", "where is", "progress"),
     "Open Track from the top menu and enter your application reference (LR-...) or bill ID. "
     "Signed-in users also see live status on My applications."),
    (("pay", "payment", "bill", "orange", "mtn", "momo", "money"),
     "Every paid application gets a bill ID. Pay it with Orange Money or MTN MoMo from the application "
     "page; your application moves forward as soon as the payment is confirmed."),
    (("selfie", "face", "biometric", "fingerprint", "liveness", "identity check"),
     "Face verification is done from home with your phone camera: you take a selfie and turn your head "
     "so we know it is really you. Fingerprints need a certified scanner, so those are captured once at "
     "a service center by appointment."),
    (("certificate", "verify", "qr", "authentic", "genuine", "fake"),
     "Approved applications receive a digitally signed e-certificate with a QR code. Anyone can check "
     "it on the Verify page by scanning the code or typing the certificate number."),
    (("password", "locked", "sign in", "login", "otp", "code"),
     "Sign in with your phone number and password. After 5 wrong passwords the account locks for 15 "
     "minutes. You can change your password and sign out of all devices from Account."),
    (("someone else", "child", "behalf", "mother", "father", "relative"),
     "Yes - when you start an application choose 'Someone else' and enter their name and your "
     "relationship. The certificate is issued in their name."),
]


@bp.post("/assistant")
@rate_limit("assistant", 40, 600)
def assistant():
    """Catalog-grounded help: answers only from the service catalog and fixed help topics."""
    q = ((request.get_json(silent=True) or {}).get("q") or "").lower().strip()[:300]
    if len(q) < 2:
        return err("Ask a question, for example: how much is a passport?")
    words = [w for w in re.findall(r"[a-z']+", q) if w not in STOP and len(w) > 1]
    scored = []
    for s in Service.query.filter_by(active=True):
        hay_name = s.name.lower()
        hay = f"{hay_name} {s.category.lower()} {s.description.lower()} {s.institution.name.lower()}"
        score = sum(3 if w in hay_name else 1 for w in words if w in hay or w.rstrip("s") in hay)
        if score:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    top = [s for _, s in scored[:3]]
    parts = []
    for keys, text in TOPICS:
        if any(k in q for k in keys):
            parts.append(text)
            break
    if top:
        s = top[0]
        fee = f"${s.fee_usd:.2f}" if s.fee_usd else "free"
        idc = ("a selfie check from home" if s.requires_face else "no identity check")
        if s.requires_fingerprint:
            idc += " plus a one-time fingerprint visit"
        parts.append(f"{s.name} is handled by the {s.institution.name}. It costs {fee}, takes about "
                     f"{s.sla_days} working days, and needs {idc}. Documents: "
                     f"{', '.join(s.required_documents) or 'none'}.")
    if not parts:
        parts.append("I could not match that to a service. Try the name of what you need, such as "
                     "'passport', 'driver license', 'business registration' or 'birth certificate'.")
    return jsonify({"answer": " ".join(parts),
                    "services": [{"code": s.code, "name": s.name} for s in top]})
