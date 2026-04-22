from flask import Flask, jsonify, request
from flask_socketio import SocketIO
from config import Config
from app.models.models import db
from app.controllers.auth_controller import auth_bp
from app.controllers.api_controller import api_bp
from app.controllers.dashboard_controller import dashboard_bp, dashboard_api, live_api
from app.controllers.admin_controller import admin_bp
import os
import logging
from sqlalchemy import inspect, text

# إعداد Logging
logging.basicConfig(level=logging.INFO)

# Global SocketIO instance
socketio = SocketIO(cors_allowed_origins="*")


def _ensure_network_onboarding_column():
    """إضافة أعمدة لقواعد بيانات قديمة (SQLite)."""
    try:
        insp = inspect(db.engine)
        if not insp.has_table("networks"):
            return
        cols = {c["name"] for c in insp.get_columns("networks")}
        with db.engine.begin() as conn:
            if "mikrotik_onboarding_complete" not in cols:
                conn.execute(
                    text(
                        "ALTER TABLE networks ADD COLUMN mikrotik_onboarding_complete BOOLEAN DEFAULT 0"
                    )
                )
            if "public_wan_ip" not in cols:
                conn.execute(
                    text("ALTER TABLE networks ADD COLUMN public_wan_ip VARCHAR(64)")
                )
    except Exception as e:
        logging.getLogger(__name__).warning("تخطي ترحيل أعمدة الشبكة: %s", e)


def create_app():
    template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), 'templates'))
    app = Flask(__name__, template_folder=template_dir)
    app.config.from_object(Config)
    app.config.setdefault("MAX_CONTENT_LENGTH", 10 * 1024)

    # إعدادات الأمان والجلسات
    app.secret_key = Config.SECRET_KEY or "your-secret-key-change-in-production"

    # تهيئة قاعدة البيانات
    db.init_app(app)

    # تهيئة SocketIO
    socketio.init_app(app)

    # تسجيل المسارات والـ blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(dashboard_api)
    app.register_blueprint(live_api)
    app.register_blueprint(admin_bp)

    # مسار رئيسي للتجربة
    @app.route("/")
    def home():
        from flask import redirect, url_for
        # إعادة التوجيه إلى لوحة التحكم إذا كان المستخدم مسجلاً
        from flask import session
        if 'user_id' in session:
            return redirect(url_for('dashboard.dashboard'))
        return redirect(url_for('auth.login'))

    # إنشاء جداول قاعدة البيانات
    with app.app_context():
        db.create_all()
        _ensure_network_onboarding_column()

        # إعداد معالجات WebSocket
        from app.utils.websocket_handler import create_socketio_handlers
        create_socketio_handlers(socketio, app)

    @app.before_request
    def hardening_request_guard():
        if request.content_length and request.content_length > 10 * 1024:
            return jsonify({"success": False, "error": "Invalid input"}), 400

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["X-XSS-Protection"] = "1; mode=block"
        resp.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com data:; script-src 'self' 'unsafe-inline' 'unsafe-eval'; img-src 'self' data: https:;"
        if "Server" in resp.headers:
            del resp.headers["Server"]
        if "X-Powered-By" in resp.headers:
            del resp.headers["X-Powered-By"]
        frontend_origin = os.getenv("FRONTEND_URL", "").strip()
        if frontend_origin:
            resp.headers["Access-Control-Allow-Origin"] = frontend_origin
            resp.headers["Vary"] = "Origin"
        return resp

    return app