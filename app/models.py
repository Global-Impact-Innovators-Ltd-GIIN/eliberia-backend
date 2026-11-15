import json
from datetime import datetime, timezone

from sqlalchemy.types import Text, TypeDecorator

from .extensions import db


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class JSONText(TypeDecorator):
    """Portable JSON column (works on SQLite and PostgreSQL)."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else json.dumps(value)

    def process_result_value(self, value, dialect):
        return None if value is None else json.loads(value)


class Institution(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    webhook_url = db.Column(db.String(300))  # where verified biometrics/decisions are dispatched

    def to_dict(self):
        return {"id": self.id, "code": self.code, "name": self.name, "description": self.description}


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(30), unique=True, nullable=False)
    email = db.Column(db.String(150))
    nin = db.Column(db.String(30), unique=True)
    dob = db.Column(db.String(20))
    county = db.Column(db.String(60))
    password_hash = db.Column(db.String(300), nullable=False)
    role = db.Column(db.String(20), default="citizen")  # citizen | officer | admin
    institution_id = db.Column(db.Integer, db.ForeignKey("institution.id"))
    phone_verified = db.Column(db.Boolean, default=False)
    otp_code = db.Column(db.String(10))
    otp_expires = db.Column(db.DateTime)
    language = db.Column(db.String(10), default="en")
    created_at = db.Column(db.DateTime, default=now)
    # account security
    failed_logins = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime)
    otp_attempts = db.Column(db.Integer, default=0)
    token_version = db.Column(db.Integer, default=0)

    def to_dict(self):
        return {
            "id": self.id, "full_name": self.full_name, "phone": self.phone, "email": self.email,
            "nin": self.nin, "dob": self.dob, "county": self.county, "role": self.role,
            "institution_id": self.institution_id, "phone_verified": self.phone_verified,
            "language": self.language,
        }


class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False)
    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(60), nullable=False)
    description = db.Column(db.Text, default="")
    institution_id = db.Column(db.Integer, db.ForeignKey("institution.id"), nullable=False)
    fee_usd = db.Column(db.Float, default=0.0)
    sla_days = db.Column(db.Integer, default=5)
    requires_face = db.Column(db.Boolean, default=False)
    requires_fingerprint = db.Column(db.Boolean, default=False)
    required_documents = db.Column(JSONText, default=list)
    form_fields = db.Column(JSONText, default=list)  # [{name,label,type,required,options}]
    active = db.Column(db.Boolean, default=True)
    institution = db.relationship("Institution")

    def to_dict(self):
        return {
            "id": self.id, "code": self.code, "name": self.name, "category": self.category,
            "description": self.description, "institution": self.institution.to_dict(),
            "fee_usd": self.fee_usd, "sla_days": self.sla_days,
            "requires_face": self.requires_face, "requires_fingerprint": self.requires_fingerprint,
            "required_documents": self.required_documents or [], "form_fields": self.form_fields or [],
        }


# draft -> submitted -> in_review -> (info_requested) -> biometric_pending -> approved|rejected
#   -> ready_for_collection -> completed
STATUSES = ["draft", "submitted", "in_review", "info_requested", "biometric_pending",
            "approved", "rejected", "ready_for_collection", "completed"]


class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(30), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    service_id = db.Column(db.Integer, db.ForeignKey("service.id"), nullable=False)
    status = db.Column(db.String(30), default="draft")
    data = db.Column(JSONText, default=dict)
    fee_usd = db.Column(db.Float, default=0.0)
    paid = db.Column(db.Boolean, default=False)
    assigned_to = db.Column(db.Integer, db.ForeignKey("user.id"))
    decision_note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=now)
    updated_at = db.Column(db.DateTime, default=now, onupdate=now)
    due_at = db.Column(db.DateTime)
    bill_id = db.Column(db.String(20))  # pay-anywhere bill number (mobile money, bank, agent)
    beneficiary = db.Column(JSONText)  # {"name","relation"} when applying on behalf of someone
    user = db.relationship("User", foreign_keys=[user_id])
    service = db.relationship("Service")
    events = db.relationship("ApplicationEvent", backref="application",
                             order_by="ApplicationEvent.id", cascade="all, delete-orphan")
    documents = db.relationship("Document", backref="application", cascade="all, delete-orphan")

    def to_dict(self, full=False):
        d = {
            "id": self.id, "reference": self.reference, "status": self.status,
            "service": {"id": self.service.id, "code": self.service.code, "name": self.service.name,
                        "institution": self.service.institution.name},
            "fee_usd": self.fee_usd, "paid": self.paid, "bill_id": self.bill_id,
            "beneficiary": self.beneficiary,
            "created_at": self.created_at.isoformat(), "updated_at": self.updated_at.isoformat(),
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "applicant": {"id": self.user.id, "full_name": self.user.full_name, "phone": self.user.phone},
        }
        if full:
            d["data"] = self.data
            d["decision_note"] = self.decision_note
            d["events"] = [e.to_dict() for e in self.events]
            d["documents"] = [x.to_dict() for x in self.documents]
            d["biometrics"] = [b.to_dict() for b in BiometricRecord.query.filter_by(application_id=self.id)]
            d["payments"] = [p.to_dict() for p in Payment.query.filter_by(application_id=self.id)]
            d["service_detail"] = self.service.to_dict()
            cert = Certificate.query.filter_by(application_id=self.id).first()
            d["certificate"] = cert.to_dict() if cert else None
            fb = Feedback.query.filter_by(application_id=self.id).first()
            d["feedback"] = fb.to_dict() if fb else None
        return d


class ApplicationEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"), nullable=False)
    status = db.Column(db.String(30))
    message = db.Column(db.String(300))
    actor = db.Column(db.String(150))
    created_at = db.Column(db.DateTime, default=now)

    def to_dict(self):
        return {"status": self.status, "message": self.message, "actor": self.actor,
                "created_at": self.created_at.isoformat()}


class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"), nullable=False)
    doc_type = db.Column(db.String(80))
    filename = db.Column(db.String(200))
    stored_name = db.Column(db.String(200))
    sha256 = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=now)

    def to_dict(self):
        return {"id": self.id, "doc_type": self.doc_type, "filename": self.filename,
                "sha256": self.sha256, "created_at": self.created_at.isoformat()}


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"), nullable=False)
    method = db.Column(db.String(30))  # orange_money | mtn_momo | card | bank
    msisdn = db.Column(db.String(30))
    amount_usd = db.Column(db.Float)
    status = db.Column(db.String(20), default="pending")  # pending | success | failed
    txn_ref = db.Column(db.String(60), unique=True)
    created_at = db.Column(db.DateTime, default=now)
    application = db.relationship("Application")

    def to_dict(self):
        return {"id": self.id, "method": self.method, "amount_usd": self.amount_usd,
                "status": self.status, "txn_ref": self.txn_ref,
                "created_at": self.created_at.isoformat()}


class BiometricRecord(db.Model):
    """Biometric evidence captured remotely (or at a center). Raw image lives on disk, DB keeps a hash."""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"))
    kind = db.Column(db.String(20))  # face | fingerprint
    source = db.Column(db.String(20), default="remote")  # remote | center
    stored_name = db.Column(db.String(200))
    sha256 = db.Column(db.String(64))
    liveness_score = db.Column(db.Float)
    match_score = db.Column(db.Float)
    status = db.Column(db.String(20), default="captured")  # captured | verified | failed | dispatched
    dispatched_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=now)

    def to_dict(self):
        return {"id": self.id, "kind": self.kind, "source": self.source, "status": self.status,
                "liveness_score": self.liveness_score, "match_score": self.match_score,
                "sha256": self.sha256,
                "dispatched_at": self.dispatched_at.isoformat() if self.dispatched_at else None,
                "created_at": self.created_at.isoformat()}


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    center = db.Column(db.String(120), nullable=False)
    slot = db.Column(db.DateTime, nullable=False)
    purpose = db.Column(db.String(40), default="biometrics")  # biometrics | collection
    status = db.Column(db.String(20), default="booked")  # booked | attended | missed | cancelled

    def to_dict(self):
        return {"id": self.id, "application_id": self.application_id, "center": self.center,
                "slot": self.slot.isoformat(), "purpose": self.purpose, "status": self.status}


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    channel = db.Column(db.String(10), default="app")  # app | sms | email
    title = db.Column(db.String(150))
    body = db.Column(db.String(400))
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=now)

    def to_dict(self):
        return {"id": self.id, "channel": self.channel, "title": self.title, "body": self.body,
                "read": self.read, "created_at": self.created_at.isoformat()}


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer)
    action = db.Column(db.String(80))
    detail = db.Column(db.String(300))
    ip = db.Column(db.String(60))
    created_at = db.Column(db.DateTime, default=now)

    def to_dict(self):
        return {"id": self.id, "user_id": self.user_id, "action": self.action, "detail": self.detail,
                "ip": self.ip, "created_at": self.created_at.isoformat()}


class Certificate(db.Model):
    """Digitally signed e-certificate issued on approval; anyone can verify it by number / QR code."""
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(30), unique=True, nullable=False)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"), unique=True, nullable=False)
    holder_name = db.Column(db.String(150), nullable=False)
    service_name = db.Column(db.String(200), nullable=False)
    institution_name = db.Column(db.String(200), nullable=False)
    issued_at = db.Column(db.DateTime, default=now)
    signature = db.Column(db.String(64), nullable=False)
    revoked = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {"number": self.number, "holder_name": self.holder_name, "service_name": self.service_name,
                "institution_name": self.institution_name, "issued_at": self.issued_at.isoformat(),
                "revoked": self.revoked}


class Feedback(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"), unique=True, nullable=False)
    rating = db.Column(db.Integer, nullable=False)  # 1-5
    comment = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=now)

    def to_dict(self):
        return {"rating": self.rating, "comment": self.comment, "created_at": self.created_at.isoformat()}
