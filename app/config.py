import os

from dotenv import load_dotenv

load_dotenv()


def _db_url():
    url = os.getenv("DATABASE_URL", "sqlite:///eliberia_dev.db")
    if url.startswith("postgres://"):  # Render / Neon style URLs
        url = url.replace("postgres://", "postgresql://", 1)
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
    PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mock")
    BIOMETRIC_PROVIDER = os.getenv("BIOMETRIC_PROVIDER", "mock")
