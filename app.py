"""
AAST CodeJudge — LTI 1.0/1.1 Auto-Judge for Moodle
Main application entry point.
"""

import os
from flask import Flask, redirect, url_for, request, session, flash
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
    from routes.proctor_routes import proctor_bp

    app.register_blueprint(lti_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(proctor_bp)

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
        # Enable screenshare if explicitly requested via query parameter
        if request.args.get('screenshare') == 'true':
            session['screenshare_required'] = True

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
                    'screenshare_required': session.get('screenshare_required', False),
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
            if data.get('screenshare_required'):
                session['screenshare_required'] = True
            session.modified = True
            request._session_token = token
        else:
            request._session_token = ''

    @app.context_processor
    def _inject_token_url_for():
        """Override url_for in templates to auto-append _lt token and screenshare=true."""
        _original = url_for

        def url_for_with_token(endpoint, **kwargs):
            # Don't add token to static files
            if endpoint == 'static':
                return _original(endpoint, **kwargs)
            token = getattr(request, '_session_token', '')
            if token and '_lt' not in kwargs:
                kwargs['_lt'] = token
            if session.get('screenshare_required') and 'screenshare' not in kwargs:
                kwargs['screenshare'] = 'true'
            return _original(endpoint, **kwargs)

        is_inst = (session.get('role') == 'instructor')
        can_switch = is_inst or (session.get('original_role') == 'instructor')

        return {
            'url_for': url_for_with_token,
            'is_instructor': is_inst,
            'can_switch_mode': can_switch,
        }

    @app.route('/switch-mode', methods=['GET', 'POST'])
    def switch_mode():
        """Allow instructors to toggle between Instructor Mode and Student Mode via role flag."""
        current_role = session.get('role')
        orig_role = session.get('original_role')

        if current_role != 'instructor' and orig_role != 'instructor':
            flash('Only instructors can switch roles.', 'danger')
            return redirect(url_for('student.problem_list'))

        target = request.args.get('target') or request.form.get('target')
        if not target:
            target = 'student' if current_role == 'instructor' else 'instructor'

        if target == 'student':
            session['original_role'] = 'instructor'
            session['role'] = 'student'
            session.modified = True
            flash('Switched to Student Mode.', 'info')
            return redirect(url_for('student.problem_list'))
        elif target == 'instructor':
            session['role'] = 'instructor'
            session['original_role'] = 'instructor'
            session.modified = True
            flash('Returned to Instructor Mode.', 'info')
            return redirect(url_for('admin.dashboard'))

        return redirect(url_for('student.problem_list'))

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
        from models.database import User, Problem, TestCase, Submission, LTISession, SharedLink, SystemSetting, ProctorSession, ProctorEvent  # noqa: F401
        db.create_all()

        # Database migrations (schema updates)
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)

        # 1. Ensure users table has regnum column
        if 'users' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('users')]
            if 'regnum' not in columns:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE users ADD COLUMN regnum VARCHAR(100)"))
                    conn.commit()
                app.logger.info("Migrated users table: added regnum column.")

        # 2. Ensure submissions table has semester column (defaulting to 'Spring 2025/2026')
        if 'submissions' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('submissions')]
            if 'semester' not in columns:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE submissions ADD COLUMN semester VARCHAR(50) NOT NULL DEFAULT 'Spring 2025/2026'"))
                    conn.commit()
                app.logger.info("Migrated submissions table: added semester column.")

        # 3. Ensure shared_links table has semester and screenshare_required columns
        if 'shared_links' in inspector.get_table_names():
            columns = [c['name'] for c in inspector.get_columns('shared_links')]
            if 'semester' not in columns:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE shared_links ADD COLUMN semester VARCHAR(50) NOT NULL DEFAULT 'Summer 2025/2026'"))
                    conn.commit()
                app.logger.info("Migrated shared_links table: added semester column.")
            if 'screenshare_required' not in columns:
                with db.engine.connect() as conn:
                    conn.execute(text("ALTER TABLE shared_links ADD COLUMN screenshare_required BOOLEAN DEFAULT 0"))
                    conn.commit()
                app.logger.info("Migrated shared_links table: added screenshare_required column.")


        # 4. Seed system settings (current_semester)
        if 'system_settings' in inspector.get_table_names():
            with db.engine.connect() as conn:
                res = conn.execute(text("SELECT 1 FROM system_settings WHERE key = 'current_semester'")).first()
                if not res:
                    conn.execute(text("INSERT INTO system_settings (key, value) VALUES ('current_semester', 'Summer 2025/2026')"))
                    conn.commit()
                    app.logger.info("Seeded current_semester in system_settings.")

        # Enable WAL mode for SQLite — allows concurrent reads during writes
        from sqlalchemy import event
        if 'sqlite' in app.config['SQLALCHEMY_DATABASE_URI']:
            with db.engine.connect() as conn:
                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.commit()

            # Ensure every new SQLite connection enables WAL
            @event.listens_for(db.engine, "connect")
            def _set_sqlite_pragma(dbapi_conn, connection_record):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

    # ------------------------------------------------------------------
    # Jinja2 custom filters
    # ------------------------------------------------------------------
    import markdown2
    from markupsafe import Markup

    @app.template_filter('markdown')
    def markdown_filter(text):
        """Convert Markdown text to HTML."""
        if not text:
            return ''
        # Normalize Windows line endings so break-on-newline works
        text = text.replace('\r\n', '\n')
        html = markdown2.markdown(
            text,
            extras=['fenced-code-blocks', 'tables', 'break-on-newline',
                    'header-ids', 'strike', 'task_list'],
        )
        return Markup(html)

    return app


# ---------------------------------------------------------------------------
# When running directly or via gunicorn (gunicorn app:app)
# ---------------------------------------------------------------------------

app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
