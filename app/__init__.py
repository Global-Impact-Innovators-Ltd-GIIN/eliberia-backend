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

    from .routes import applications, auth, catalog, staff
    for m in (auth, catalog, applications, staff):
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
        return resp

    with app.app_context():
        db.create_all()
        from .seed import seed
        seed()
    return app
