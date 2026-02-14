"""
AAST CodeJudge — LTI 1.0/1.1 Auto-Judge for Moodle
Main application entry point.
"""

import os
from flask import Flask, redirect, url_for, request
from config import Config
from models.database import db  # Single shared SQLAlchemy instance


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialise extensions (uses the db created in models/database.py)
    db.init_app(app)

    # ------------------------------------------------------------------
    # Register blueprints
    # ------------------------------------------------------------------
    from routes.lti_routes import lti_bp
    from routes.admin_routes import admin_bp
    from routes.student_routes import student_bp

    app.register_blueprint(lti_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(student_bp)

    # ------------------------------------------------------------------
    # Root redirect
    # ------------------------------------------------------------------
    @app.route("/", methods=["GET", "POST"])
    def index():
        """Redirect to LTI launch info or dev login page.

        Also acts as fallback if Moodle sends the LTI POST to '/' instead
        of '/lti/launch'.
        """
        if request.method == "POST":
            # Forward LTI launch to the real endpoint
            return redirect(url_for("lti.launch"), code=307)  # 307 preserves POST

        if app.config.get("ENV") == "development" or app.debug:
            return redirect(url_for("lti.test_launch_page"))
        return (
            "<h3>AAST CodeJudge</h3>"
            "<p>This application is an LTI tool. "
            "Please launch it from your Moodle course.</p>"
        ), 200

    # ------------------------------------------------------------------
    # Create database tables on first request (if they don't exist)
    # ------------------------------------------------------------------
    with app.app_context():
        # Import models so SQLAlchemy knows about them
        from models.database import User, Problem, TestCase, Submission, LTISession  # noqa: F401
        db.create_all()

    return app


# ---------------------------------------------------------------------------
# When running directly or via gunicorn (gunicorn app:app)
# ---------------------------------------------------------------------------

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
