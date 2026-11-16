"""Encryption at rest for personal data (form answers, uploaded documents, biometric images).

Key: DATA_KEY env var, falling back to SECRET_KEY. Losing or changing the key makes existing data
unreadable, so set DATA_KEY once and back it up. Values written before encryption was introduced are
still readable (plain values are passed through).
"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from ..config import Config

TEXT_PREFIX = "enc1:"
BYTES_PREFIX = b"ELV1"


def _fernet():
    key = (Config.DATA_KEY or Config.SECRET_KEY).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key).digest()))


def encrypt_text(value: str) -> str:
    return TEXT_PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt_text(value: str) -> str:
    if not value or not value.startswith(TEXT_PREFIX):
        return value  # legacy plaintext
    try:
        return _fernet().decrypt(value[len(TEXT_PREFIX):].encode()).decode()
    except InvalidToken:
        raise ValueError("Stored data could not be decrypted - the data key has changed")


def encrypt_bytes(raw: bytes) -> bytes:
    return BYTES_PREFIX + _fernet().encrypt(raw)


def decrypt_bytes(blob: bytes) -> bytes:
    if not blob.startswith(BYTES_PREFIX):
        return blob  # legacy plaintext file
    return _fernet().decrypt(blob[len(BYTES_PREFIX):])
