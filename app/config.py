import os

from dotenv import load_dotenv

load_dotenv()


def _db_url():
    url = os.getenv("DATABASE_URL", "sqlite:///eliberia_dev.db")
    # Render / Neon give postgres:// or postgresql://; force the psycopg2 driver we install.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+psycopg2://" + url[len(prefix):]
            break
    return url


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = _db_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")]
    UPLOAD_DIR = os.getenv(
        "UPLOAD_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads"))
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024
    TOKEN_HOURS = int(os.getenv("TOKEN_HOURS", "12"))
    # When True, OTP codes are returned in API responses (development only!)
    EXPOSE_OTP = os.getenv("EXPOSE_OTP", "True").lower() == "true"
    # staff, admins and agents confirm sign-in with a one-time code
    STAFF_2FA = os.getenv("STAFF_2FA", "True").lower() == "true"
    # shared secret the USSD gateway appends as ?key=...; without it the caller's number is not trusted
    USSD_SECRET = os.getenv("USSD_SECRET") or None
    PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mock")
    BIOMETRIC_PROVIDER = os.getenv("BIOMETRIC_PROVIDER", "mock")
