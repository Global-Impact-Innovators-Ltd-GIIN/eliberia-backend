"""E-certificates: issued when an application is approved, verifiable by anyone via number or QR code.

The signature is an HMAC over the certificate's contents with the server secret, so a certificate row
that is altered in the database (or a forged number) fails verification.
"""
import hashlib
import hmac
import secrets

from flask import current_app

from ..extensions import db
from ..models import Certificate, now


def _sign(number, application_id, holder, service, issued_at):
    msg = "|".join([number, str(application_id), holder, service, issued_at.isoformat()])
    return hmac.new(current_app.config["SECRET_KEY"].encode(), msg.encode(), hashlib.sha256).hexdigest()


def issue(application):
    existing = Certificate.query.filter_by(application_id=application.id).first()
    if existing:
        return existing
    issued = now().replace(microsecond=0)
    holder = (application.beneficiary or {}).get("name") or application.user.full_name
    number = f"ELC-{issued:%Y}-{secrets.token_hex(4).upper()}"
    cert = Certificate(number=number, application_id=application.id, holder_name=holder,
                       service_name=application.service.name,
                       institution_name=application.service.institution.name, issued_at=issued,
                       signature=_sign(number, application.id, holder, application.service.name, issued))
    db.session.add(cert)
    return cert


def is_authentic(cert):
    expected = _sign(cert.number, cert.application_id, cert.holder_name, cert.service_name, cert.issued_at)
    return hmac.compare_digest(expected, cert.signature)
