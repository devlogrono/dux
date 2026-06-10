"""DUX Flask application package.

Provides the app factory `create_app` and shared extensions `db`, `bcrypt`,
`login_manager` and `cache` used across controllers.
"""
from __future__ import annotations

from flask import Flask, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, current_user
from flask_caching import Cache

from .config import Config

# Extensions (unbound; initialized in create_app)
# These are imported by modules like `from dux import db`
db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()
cache = Cache()


def create_app(config_object: type[Config] | None = None) -> Flask:
    app = Flask(
        __name__,
        static_folder="static",
        template_folder="templates",
        instance_relative_config=False,
    )

    def _iso2_to_flag(code: str) -> str:
        if not code or len(code) != 2:
            return code or ""
        # Convierte 'ES' -> 🇪🇸 usando indicadores regionales Unicode
        base = 127397
        return "".join(chr(base + ord(c.upper())) for c in code)

    app.jinja_env.filters["flag_emoji"] = _iso2_to_flag

    # Load configuration
    app.config.from_object(config_object or Config)

    # Init extensions
    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login_get"
    login_manager.login_message_category = "info"
    app.config.setdefault("CACHE_TYPE", "SimpleCache")
    app.config.setdefault("CACHE_DEFAULT_TIMEOUT", 3600)
    cache.init_app(app)

    # User loader based on reflected `users` table
    @login_manager.user_loader
    def load_user(user_id: str):
        try:
            from dux.models import Base  # lazy import to avoid circulars
        except Exception:
            return None
        User = getattr(Base.classes, "users", None)
        if User is None:
            return None
        try:
            # SQLAlchemy 2.x: Session via Flask-SQLAlchemy
            return db.session.get(User, user_id)
        except Exception:
            return None

    # Reflect database metadata once app/engine is ready
    with app.app_context():
        try:
            from dux.models import reflect_db
            reflect_db(app)
        except Exception as exc:  # reflection may fail if DB not reachable yet
            app.logger.warning("DB reflection skipped/failed: %s", exc)

    # Expose security helpers and utilities to Jinja templates
    def _can_mod(module: str) -> bool:
        try:
            from dux.common.security import has_any_prefix  # lazy import to avoid circulars
            return has_any_prefix(current_user, module)
        except Exception:
            return False

    def _can(perm_name: str) -> bool:
        try:
            from dux.common.security import has_perm  # lazy import
            return has_perm(current_user, perm_name)
        except Exception:
            return False

    app.jinja_env.globals.update(
        can_mod=_can_mod,
        can=_can,
    )

    # Register blueprints (import inside function to avoid circular imports)
    from .controllers.auth_controller import auth_bp
    from .controllers.landing_controller import landing_bp
    from .controllers.admin_controller import admin_bp
    from .controllers.dashboard_actas_controller import bp as dashboard_actas_bp
    from .controllers.dashboard_futbolistas_controller import bp as dashboard_futbolistas_bp
    from .controllers.dashboard_physical_controller import bp as dashboard_physical_bp
    from .controllers.dashboard_lesiones_controller import bp as dashboard_lesiones_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(landing_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(dashboard_actas_bp)
    app.register_blueprint(dashboard_futbolistas_bp)
    app.register_blueprint(dashboard_physical_bp)
    app.register_blueprint(dashboard_lesiones_bp)
    # Root route -> redirect to dashboard or login
    @app.get("/")
    def index():
        return redirect(url_for("dashboard_actas.index"))

    return app
