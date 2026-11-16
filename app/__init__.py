from flask import Flask, jsonify
from flask_cors import CORS

from .config import Config
from .extensions import db


def create_app(config=None):
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    CORS(app, origins=app.config["CORS_ORIGINS"])
    db.init_app(app)

    from .routes import agent, applications, auth, catalog, citizen, extras, ops, org, public, staff
    for m in (auth, public, catalog, applications, staff, agent, extras, org, citizen, ops):
        app.register_blueprint(m.bp)

    @app.get("/api/health")
    def health():
        ok = not app.config.get("DATA_KEY_MISMATCH")
        return jsonify({"status": "ok" if ok else "degraded", "service": "eLiberia API", "data_key_ok": ok})

    @app.errorhandler(404)
    def nf(_):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(413)
    def too_big(_):
        return jsonify({"error": "File too large (max 8MB)"}), 413

    @app.errorhandler(500)
    def boom(_):
        db.session.rollback()
        return jsonify({"error": "Internal server error"}), 500

    from .services import sms as _sms
    app.after_request(_sms.flush)

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return resp

    with app.app_context():
        db.create_all()
        _ensure_columns()
        _check_data_key(app)
        from .seed import seed
        seed()
    return app


def _check_data_key(app):
    """Remember a fingerprint of the encryption key; warn loudly if it ever changes."""
    import hashlib
    from .models import SystemSetting
    key = (Config.DATA_KEY or Config.SECRET_KEY).encode()
    fp = hashlib.sha256(b"eliberia-key-fp:" + key).hexdigest()[:16]
    row = db.session.get(SystemSetting, "data_key_fingerprint")
    if row is None:
        db.session.add(SystemSetting(key="data_key_fingerprint", value=fp))
        db.session.commit()
    elif row.value != fp:
        app.config["DATA_KEY_MISMATCH"] = True
        app.logger.critical("The data encryption key has CHANGED. Stored answers and files cannot be read. "
                            "Restore the previous DATA_KEY / SECRET_KEY value.")


def _ensure_columns():
    """Tiny forward-only migration: add columns introduced after a database was first created."""
    from sqlalchemy import inspect, text
    wanted = {
        "user": {"failed_logins": "INTEGER DEFAULT 0", "locked_until": "TIMESTAMP",
                 "otp_attempts": "INTEGER DEFAULT 0", "token_version": "INTEGER DEFAULT 0",
                 "otp_purpose": "VARCHAR(12)", "nationality": "VARCHAR(2)", "id_doc_type": "VARCHAR(20)",
                 "id_number": "VARCHAR(40)", "admin_perms": "TEXT", "disabled": "BOOLEAN DEFAULT FALSE",
                 "must_change_password": "BOOLEAN DEFAULT FALSE", "is_demo": "BOOLEAN DEFAULT FALSE",
                 "terms_version": "VARCHAR(20)", "terms_accepted_at": "TIMESTAMP"},
        "payment": {"provider": "VARCHAR(20)", "provider_ref": "VARCHAR(80)", "currency": "VARCHAR(3)",
                    "settled_at": "TIMESTAMP", "refunded_at": "TIMESTAMP", "refunded_by": "INTEGER",
                    "refund_reason": "VARCHAR(300)"},
        "certificate": {"expires_at": "TIMESTAMP", "reminders_sent": "TEXT"},
        "application": {"bill_id": "VARCHAR(20)", "beneficiary": "TEXT", "agent_id": "INTEGER",
                        "stage_index": "INTEGER DEFAULT 0", "stage_actors": "TEXT", "delivery": "TEXT"},
        "institution": {"guidelines": "TEXT", "contact_phone": "VARCHAR(30)", "contact_email": "VARCHAR(120)"},
        "service": {"seed_version": "INTEGER DEFAULT 0", "workflow": "TEXT", "validity_months": "INTEGER",
                    "renewal_code": "VARCHAR(40)"},
    }
    insp = inspect(db.engine)
    for table, cols in wanted.items():
        have = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols.items():
            if name not in have:
                db.session.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl}'))
    db.session.commit()
