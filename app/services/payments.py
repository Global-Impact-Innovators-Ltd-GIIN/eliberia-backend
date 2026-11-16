"""Payments.

Each payment method is handled by one provider:

  mock     simulated push payment; the citizen presses "I approved it". Default for every method.
  mtn      MTN Mobile Money Collection API ("request to pay"). Turned on for `mtn_momo` by setting
           MTN_MOMO_SUBSCRIPTION_KEY, MTN_MOMO_API_USER, MTN_MOMO_API_KEY. MTN_MOMO_ENV=sandbox|production,
           MTN_MOMO_TARGET (e.g. mtnliberia), MTN_MOMO_CURRENCY (EUR in the sandbox, USD or LRD live).
  webhook  a payment aggregator (or Orange Money once its merchant API is contracted) confirms by calling
           POST /api/payments/webhook with an HMAC-SHA256 signature made with PAYMENT_WEBHOOK_SECRET.
           Used for `orange_money` (and `card` / `bank`) when PAYMENT_PROVIDER=live.
  cash     an agent collected cash and confirms it themselves (always allowed, agents only, audited).

A payment only becomes `success` through the provider: the browser can never mark a live payment paid.
"""
import base64
import hashlib
import hmac
import json
import secrets
import urllib.error
import urllib.request
import uuid

from flask import current_app

from ..extensions import db
from ..models import Payment, now

METHODS = {"orange_money": "Orange Money", "mtn_momo": "MTN MoMo", "card": "Card", "bank": "Bank transfer",
           "agent_cash": "Cash collected by agent"}


class ProviderError(Exception):
    pass


def provider_for(method):
    cfg = current_app.config
    if method == "agent_cash":
        return "cash"
    if method == "mtn_momo" and cfg.get("MTN_MOMO_SUBSCRIPTION_KEY") and cfg.get("MTN_MOMO_API_USER"):
        return "mtn"
    if (cfg.get("PAYMENT_PROVIDER") or "mock") == "live":
        return "webhook"
    return "mock"


def initiate(application, method, msisdn):
    provider = provider_for(method)
    p = Payment(application_id=application.id, method=method, msisdn=msisdn, provider=provider,
                amount_usd=application.fee_usd, status="pending", currency="USD",
                txn_ref="TX-" + secrets.token_hex(6).upper())
    db.session.add(p)
    db.session.flush()
    if provider == "mtn":
        p.currency = current_app.config.get("MTN_MOMO_CURRENCY") or "USD"
        p.provider_ref = _mtn_request_to_pay(p, application)
    return p


def settle(payment, success=True, provider_ref=None):
    if payment.status != "pending":
        return payment
    payment.status = "success" if success else "failed"
    payment.settled_at = now()
    if provider_ref:
        payment.provider_ref = provider_ref
    if success:
        payment.application.paid = True
    return payment


def refresh(payment):
    """Ask the provider for the latest state of a pending payment. Returns True if it changed."""
    if payment.status != "pending" or payment.provider != "mtn" or not payment.provider_ref:
        return False
    state = _mtn_status(payment.provider_ref)
    if state == "SUCCESSFUL":
        settle(payment, True)
        return True
    if state in ("FAILED", "REJECTED", "TIMEOUT"):
        settle(payment, False)
        return True
    return False


def verify_webhook(raw_body: bytes, signature: str):
    secret = current_app.config.get("PAYMENT_WEBHOOK_SECRET")
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


# ------------------------------------------------------------------ MTN MoMo
def _mtn_base():
    env = current_app.config.get("MTN_MOMO_ENV") or "sandbox"
    return "https://sandbox.momodeveloper.mtn.com" if env == "sandbox" else "https://proxy.momoapi.mtn.com"


def _mtn_headers(token=None):
    cfg = current_app.config
    h = {"Ocp-Apim-Subscription-Key": cfg["MTN_MOMO_SUBSCRIPTION_KEY"],
         "X-Target-Environment": cfg.get("MTN_MOMO_TARGET") or "sandbox", "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _mtn_token():
    cfg = current_app.config
    basic = base64.b64encode(f"{cfg['MTN_MOMO_API_USER']}:{cfg['MTN_MOMO_API_KEY']}".encode()).decode()
    req = urllib.request.Request(f"{_mtn_base()}/collection/token/", data=b"", method="POST", headers={
        "Ocp-Apim-Subscription-Key": cfg["MTN_MOMO_SUBSCRIPTION_KEY"], "Authorization": f"Basic {basic}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())["access_token"]
    except Exception as exc:
        raise ProviderError(f"MTN MoMo sign-in failed: {exc}")


def _mtn_request_to_pay(payment, application):
    ref = str(uuid.uuid4())
    msisdn = "".join(ch for ch in (payment.msisdn or "") if ch.isdigit())
    body = {"amount": f"{payment.amount_usd:.2f}", "currency": payment.currency, "externalId": payment.txn_ref,
            "payer": {"partyIdType": "MSISDN", "partyId": msisdn},
            "payerMessage": f"eLiberia {application.reference}"[:160],
            "payeeNote": application.service.name[:160]}
    headers = {**_mtn_headers(_mtn_token()), "X-Reference-Id": ref}
    callback = current_app.config.get("MTN_MOMO_CALLBACK_URL")
    if callback:
        headers["X-Callback-Url"] = callback
    req = urllib.request.Request(f"{_mtn_base()}/collection/v1_0/requesttopay", data=json.dumps(body).encode(),
                                 method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status != 202:
                raise ProviderError(f"MTN MoMo refused the request ({r.status})")
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"MTN MoMo refused the request ({exc.code})")
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"MTN MoMo could not be reached: {exc}")
    return ref


def _mtn_status(ref):
    req = urllib.request.Request(f"{_mtn_base()}/collection/v1_0/requesttopay/{ref}",
                                 headers=_mtn_headers(_mtn_token()))
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("status")
    except Exception as exc:
        current_app.logger.warning("MTN status check failed: %s", exc)
        return None


def status():
    cfg = current_app.config
    return {
        "mtn_momo": provider_for("mtn_momo"),
        "orange_money": provider_for("orange_money"),
        "webhook_secret_set": bool(cfg.get("PAYMENT_WEBHOOK_SECRET")),
        "live": provider_for("mtn_momo") != "mock" or provider_for("orange_money") != "mock",
    }
