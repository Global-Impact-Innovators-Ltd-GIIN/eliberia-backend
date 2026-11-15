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

    from .routes import applications, auth, catalog, public, staff
    for m in (auth, public, catalog, applications, staff):
        app.register_blueprint(m.bp)

    @app.get("/api/health")
    def health():
        return jsonify({"status": "ok", "service": "eLiberia API"})

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
        from .seed import seed
        seed()
    return app


def _ensure_columns():
    """Tiny forward-only migration: add columns introduced after a database was first created."""
    from sqlalchemy import inspect, text
    wanted = {
        "user": {"failed_logins": "INTEGER DEFAULT 0", "locked_until": "TIMESTAMP",
                 "otp_attempts": "INTEGER DEFAULT 0", "token_version": "INTEGER DEFAULT 0"},
        "application": {"bill_id": "VARCHAR(20)", "beneficiary": "TEXT"},
    }
    insp = inspect(db.engine)
    for table, cols in wanted.items():
        have = {c["name"] for c in insp.get_columns(table)}
        for name, ddl in cols.items():
            if name not in have:
                db.session.execute(text(f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl}'))
    db.session.commit()
