"""Biometric capture / verification / dispatch.

Remote flow (no trip to an office):
  1. Citizen captures a face selfie in the browser (liveness challenge run client-side, score sent along).
  2. Server stores the image, hashes it, and calls the configured provider to verify liveness and
     (when a reference exists, e.g. NIN photo) compute a match score.
  3. Verified records are *dispatched* to the institution (webhook or queue) so the agency receives the
     biometric without the citizen visiting. Services that legally require a physical fingerprint scan
     fall back to a booked appointment at a service center.

The `mock` provider is deterministic and for development only. For production plug in a certified
vendor (e.g. a face-match + liveness API) by implementing `verify()`.
"""
import hashlib
import json
import os
import urllib.request

from flask import current_app

from ..models import now

FACE_LIVENESS_MIN = 0.80
FACE_MATCH_MIN = 0.75


def save_image(raw: bytes, prefix: str):
    digest = hashlib.sha256(raw).hexdigest()
    name = f"{prefix}_{digest[:16]}.bin"
    os.makedirs(current_app.config["UPLOAD_DIR"], exist_ok=True)
    with open(os.path.join(current_app.config["UPLOAD_DIR"], name), "wb") as f:
        f.write(raw)
    return name, digest


def verify(kind, raw: bytes, client_liveness: float):
    """Return (liveness_score, match_score). Mock: trusts client liveness, derives match from image hash."""
    provider = current_app.config["BIOMETRIC_PROVIDER"]
    if provider != "mock":
        raise NotImplementedError(f"Biometric provider '{provider}' is not configured")
    liveness = max(0.0, min(1.0, float(client_liveness or 0)))
    match = 0.80 + (int(hashlib.sha256(raw).hexdigest()[:4], 16) % 20) / 100.0  # 0.80-0.99
    return liveness, round(match, 2)


def is_verified(kind, liveness, match):
    if kind == "face":
        return liveness >= FACE_LIVENESS_MIN and match >= FACE_MATCH_MIN
    return liveness >= 0.5 and match >= 0.7


def dispatch(record, institution):
    """Send verified biometric evidence (hash + scores, never raw image) to the institution."""
    payload = {"application_id": record.application_id, "kind": record.kind, "sha256": record.sha256,
               "liveness": record.liveness_score, "match": record.match_score, "source": record.source}
    if institution and institution.webhook_url:
        try:
            req = urllib.request.Request(institution.webhook_url, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
        except Exception as exc:  # keep going; the record stays 'verified' and can be retried
            current_app.logger.warning("dispatch failed: %s", exc)
            return False
    record.status = "dispatched"
    record.dispatched_at = now()
    return True
