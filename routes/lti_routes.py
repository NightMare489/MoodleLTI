"""
LTI Launch Routes.

Handles the LTI launch POST from Moodle, validates OAuth,
creates/updates user, stores session data, and redirects
based on role.
"""

from flask import Blueprint, request, session, redirect, url_for, render_template, make_response, current_app
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from models.database import db, User, LTISession, Problem
from lti.auth import validate_lti_request, extract_lti_user_data

lti_bp = Blueprint('lti', __name__)


def _make_launch_token(data):
    """Create a signed, time-limited token carrying session data.

    Used to pass the session through a URL query parameter when
    third-party cookies are blocked (e.g. Chrome incognito inside an
    iframe).  Token is valid for 24 hours (the full session lifetime).
    """
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    return s.dumps(data, salt='lti-launch')


def verify_launch_token(token, max_age=86400):
    """Verify and decode a launch token.

    Returns the payload dict, or None if invalid / expired.
    Default max_age is 24 hours.
    """
    s = URLSafeTimedSerializer(current_app.config['SECRET_KEY'])
    try:
        return s.loads(token, salt='lti-launch', max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None


def _client_side_redirect(target_url):
    """Return an HTML page that redirects via JavaScript.

    In cross-site iframes, browsers (especially Chrome) may discard
    Set-Cookie headers on 302 redirects.  By returning a 200 with the
    cookie in the response *and* redirecting via JS, the browser stores
    the cookie before navigating.
    """
    html = f"""<!DOCTYPE html>
<html>
<head><title>Redirecting…</title></head>
<body>
<p>Redirecting…</p>
<script>window.location.replace("{target_url}");</script>
<noscript><a href="{target_url}">Click here to continue</a></noscript>
</body>
</html>"""
    resp = make_response(html, 200)
    resp.headers['Content-Type'] = 'text/html'
    return resp


@lti_bp.route('/lti/launch', methods=['POST'])
def launch():
    """Handle LTI launch from Moodle."""
    # Validate the OAuth signature
    is_valid, error, params = validate_lti_request(request)

    if not is_valid:
        return render_template('error.html', error=f'LTI Launch Failed: {error}'), 403

    # Extract user data
    user_data = extract_lti_user_data(params)

    # Find or create user
    user = User.query.filter_by(lti_user_id=user_data['lti_user_id']).first()
    if user:
        # Update existing user info
        user.name = user_data['name']
        user.email = user_data['email']
        user.role = user_data['role']
    else:
        user = User(
            lti_user_id=user_data['lti_user_id'],
            name=user_data['name'],
            email=user_data['email'],
            role=user_data['role'],
        )
        db.session.add(user)
        db.session.flush()  # Get user.id

    # Store LTI session for grade passback
    lti_session = LTISession(
        user_id=user.id,
        context_id=user_data['context_id'],
        resource_link_id=user_data['resource_link_id'],
        outcome_service_url=user_data['outcome_service_url'],
        result_sourcedid=user_data['result_sourcedid'],
    )
    db.session.add(lti_session)
    db.session.commit()

    # If a specific problem was requested, lock the student to it
    locked_problem = params.get('custom_problem_id') or None

    # Session data
    sess_data = {
        'user_id': user.id,
        'user_name': user.name,
        'role': user.role,
        'lti_session_id': lti_session.id,
        'locked_problem_id': int(locked_problem) if locked_problem else None,
    }

    # Try to set the Flask cookie session (works when cookies aren't blocked)
    session['user_id'] = sess_data['user_id']
    session['user_name'] = sess_data['user_name']
    session['role'] = sess_data['role']
    session['lti_session_id'] = sess_data['lti_session_id']
    session['locked_problem_id'] = sess_data['locked_problem_id']
    session.modified = True

    # Also create a signed URL token as a fallback for when
    # third-party cookies are blocked (Chrome incognito in iframe).
    token = _make_launch_token(sess_data)

    # Determine redirect target
    if user.role == 'instructor':
        target = url_for('admin.dashboard', _lt=token)
    else:
        if locked_problem:
            target = url_for('student.view_problem', problem_id=locked_problem, _lt=token)
        else:
            target = url_for('student.problem_list', _lt=token)

    return _client_side_redirect(target)


@lti_bp.route('/test', methods=['GET'])
def test_launch_page():
    """Dev-only test page to simulate LTI launches.

    This should be disabled in production.
    """
    return render_template('test_launch.html')


@lti_bp.route('/dev/login/<role>', methods=['GET'])
def dev_login(role):
    """Dev-only shortcut to log in without LTI (for testing).

    This should be disabled in production.
    """
    if role not in ('instructor', 'student'):
        role = 'student'

    # Find or create a dev user
    dev_user_id = f'dev-{role}'
    user = User.query.filter_by(lti_user_id=dev_user_id).first()
    if not user:
        user = User(
            lti_user_id=dev_user_id,
            name=f'Dev {role.title()}',
            email=f'{role}@dev.local',
            role=role,
        )
        db.session.add(user)
        db.session.flush()

    lti_session = LTISession(
        user_id=user.id,
        context_id='dev-context',
        resource_link_id='dev-resource',
    )
    db.session.add(lti_session)
    db.session.commit()

    session['user_id'] = user.id
    session['user_name'] = user.name
    session['role'] = user.role
    session['lti_session_id'] = lti_session.id

    if role == 'instructor':
        return redirect(url_for('admin.dashboard'))
    return redirect(url_for('student.problem_list'))
