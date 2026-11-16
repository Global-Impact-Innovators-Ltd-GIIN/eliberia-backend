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
    # key for encrypting personal data at rest; falls back to SECRET_KEY. Never change it once data exists.
    DATA_KEY = os.getenv("DATA_KEY") or None
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
    PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mock")  # mock | live
    PAYMENT_WEBHOOK_SECRET = os.getenv("PAYMENT_WEBHOOK_SECRET") or None
    MTN_MOMO_SUBSCRIPTION_KEY = os.getenv("MTN_MOMO_SUBSCRIPTION_KEY") or None
    MTN_MOMO_API_USER = os.getenv("MTN_MOMO_API_USER") or None
    MTN_MOMO_API_KEY = os.getenv("MTN_MOMO_API_KEY") or None
    MTN_MOMO_ENV = os.getenv("MTN_MOMO_ENV", "sandbox")
    MTN_MOMO_TARGET = os.getenv("MTN_MOMO_TARGET", "sandbox")
    MTN_MOMO_CURRENCY = os.getenv("MTN_MOMO_CURRENCY", "EUR")
    MTN_MOMO_CALLBACK_URL = os.getenv("MTN_MOMO_CALLBACK_URL") or None
    # SMS: log | africastalking | twilio  (see services/sms.py)
    SMS_PROVIDER = os.getenv("SMS_PROVIDER", "log")
    SMS_USERNAME = os.getenv("SMS_USERNAME") or None
    SMS_API_KEY = os.getenv("SMS_API_KEY") or None
    SMS_SENDER_ID = os.getenv("SMS_SENDER_ID") or None
    SMS_ACCOUNT_SID = os.getenv("SMS_ACCOUNT_SID") or None
    SMS_AUTH_TOKEN = os.getenv("SMS_AUTH_TOKEN") or None
    SMS_FROM = os.getenv("SMS_FROM") or None
    SMS_UPDATES = os.getenv("SMS_UPDATES", "True").lower() == "true"  # also text status updates
    # files: local | s3  (see services/storage.py)
    STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")
    STORAGE_S3_BUCKET = os.getenv("STORAGE_S3_BUCKET") or None
    STORAGE_S3_ENDPOINT = os.getenv("STORAGE_S3_ENDPOINT") or None
    STORAGE_S3_ACCESS_KEY = os.getenv("STORAGE_S3_ACCESS_KEY") or None
    STORAGE_S3_SECRET_KEY = os.getenv("STORAGE_S3_SECRET_KEY") or None
    STORAGE_S3_REGION = os.getenv("STORAGE_S3_REGION", "auto")
    # demo accounts and first administrator
    DEMO_MODE = os.getenv("DEMO_MODE", os.getenv("EXPOSE_OTP", "True")).lower() == "true"
    BOOTSTRAP_ADMIN_PHONE = os.getenv("BOOTSTRAP_ADMIN_PHONE") or None
    BOOTSTRAP_ADMIN_NAME = os.getenv("BOOTSTRAP_ADMIN_NAME", "Platform Administrator")
    BOOTSTRAP_ADMIN_PASSWORD = os.getenv("BOOTSTRAP_ADMIN_PASSWORD") or None
    FORCE_PASSWORD_CHANGE = os.getenv("FORCE_PASSWORD_CHANGE", "True").lower() == "true"
    # legal and retention
    TERMS_VERSION = os.getenv("TERMS_VERSION", "2026-11-draft")
    DRAFT_RETENTION_DAYS = int(os.getenv("DRAFT_RETENTION_DAYS", "90"))
    NOTIFICATION_RETENTION_DAYS = int(os.getenv("NOTIFICATION_RETENTION_DAYS", "365"))
    TASKS_SECRET = os.getenv("TASKS_SECRET") or None
    # citizen services
    CENTER_SLOT_CAPACITY = int(os.getenv("CENTER_SLOT_CAPACITY", "5"))
    COURIER_FEE_USD = float(os.getenv("COURIER_FEE_USD", "5"))
    BIOMETRIC_PROVIDER = os.getenv("BIOMETRIC_PROVIDER", "mock")
