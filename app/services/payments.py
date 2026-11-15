"""Payment provider abstraction.

`mock` provider simulates mobile-money (Orange Money / MTN MoMo) push payments: the payment is created
as `pending` and confirmed by calling the confirm endpoint (stands in for the provider webhook).
Swap in a real provider by implementing `initiate()` and a webhook that calls `settle()`.
"""
import secrets

from ..extensions import db
from ..models import Payment

METHODS = {"orange_money": "Orange Money", "mtn_momo": "MTN MoMo", "card": "Card", "bank": "Bank transfer"}


def initiate(application, method, msisdn):
    p = Payment(application_id=application.id, method=method, msisdn=msisdn,
                amount_usd=application.fee_usd, status="pending",
                txn_ref="TX-" + secrets.token_hex(6).upper())
    db.session.add(p)
    db.session.flush()
    return p


def settle(payment, success=True):
    payment.status = "success" if success else "failed"
    if success:
        payment.application.paid = True
    return payment
