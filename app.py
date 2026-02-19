"""
AAST CodeJudge — LTI 1.0/1.1 Auto-Judge for Moodle
Main application entry point.
"""

import os
from flask import Flask, redirect, url_for, request, session
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
    # Cookie-less session fallback via signed URL token (_lt param).
    # Chrome incognito blocks third-party cookies inside iframes so
    # the normal Flask session cookie won't survive. We pass a signed
    # `_lt` query parameter on every link and restore the session from
    # it when the cookie is missing.
    # ------------------------------------------------------------------
    from routes.lti_routes import verify_launch_token, _make_launch_token

    @app.before_request
    def _restore_session_from_token():
        """If Flask cookie session is empty, try the _lt URL token."""
        if 'user_id' in session:
            # Cookie session works — store the token on request for
            # propagation but don't overwrite the session.
            token = request.args.get('_lt', '')
            if token:
                request._session_token = token
            else:
                # Mint a fresh token so template links still carry it
                request._session_token = _make_launch_token({
                    'user_id': session['user_id'],
                    'user_name': session.get('user_name', ''),
                    'role': session.get('role', 'student'),
                    'lti_session_id': session.get('lti_session_id'),
                    'locked_problem_ids': session.get('locked_problem_ids'),
                })
            return

        token = request.args.get('_lt', '')
        if not token:
            request._session_token = ''
            return

        data = verify_launch_token(token)
        if data:
            session['user_id'] = data['user_id']
            session['user_name'] = data.get('user_name', '')
            session['role'] = data.get('role', 'student')
            session['lti_session_id'] = data.get('lti_session_id')
            session['locked_problem_ids'] = data.get('locked_problem_ids')
            session.modified = True
            request._session_token = token
        else:
            request._session_token = ''

    @app.context_processor
    def _inject_token_url_for():
        """Override url_for in templates to auto-append _lt token."""
        _original = url_for

        def url_for_with_token(endpoint, **kwargs):
            # Don't add token to static files
            if endpoint == 'static':
                return _original(endpoint, **kwargs)
            token = getattr(request, '_session_token', '')
            if token and '_lt' not in kwargs:
                kwargs['_lt'] = token
            return _original(endpoint, **kwargs)

        return {'url_for': url_for_with_token}

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
