"""
LTI 1.0/1.1 OAuth 1.0a Authentication Module.

Validates incoming LTI launch requests from Moodle by verifying
the OAuth 1.0a signature using the shared consumer key and secret.
"""

import time
import hashlib
import hmac
import urllib.parse
from functools import wraps
from flask import request, session, redirect, url_for, abort, current_app


def _normalize_params(params):
    """Normalize OAuth parameters for base string construction.

    Per OAuth 1.0a spec: parameters are sorted by key, then by value,
    and encoded using percent-encoding.
    """
    # Exclude oauth_signature from the normalized params
    filtered = {k: v for k, v in params.items() if k != 'oauth_signature'}
    sorted_params = sorted(filtered.items())
    return '&'.join(
        f'{urllib.parse.quote(str(k), safe="")}={urllib.parse.quote(str(v), safe="")}'
        for k, v in sorted_params
    )


def _build_base_string(method, url, params):
    """Build the OAuth signature base string.

    Format: METHOD&url_encoded_url&url_encoded_params
    """
    # Strip query string and fragment from URL
    parsed = urllib.parse.urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    normalized = _normalize_params(params)
    return '&'.join([
        urllib.parse.quote(method.upper(), safe=''),
        urllib.parse.quote(base_url, safe=''),
        urllib.parse.quote(normalized, safe=''),
    ])


def _sign(base_string, consumer_secret):
    """Generate HMAC-SHA1 signature."""
    # LTI 1.0 uses consumer secret + '&' (no token secret)
    signing_key = f'{urllib.parse.quote(consumer_secret, safe="")}&'
    hashed = hmac.new(
        signing_key.encode('utf-8'),
        base_string.encode('utf-8'),
        hashlib.sha1
    )
    import base64
    return base64.b64encode(hashed.digest()).decode('utf-8')


def validate_lti_request(req):
    """Validate an incoming LTI launch request.

    Returns:
        tuple: (is_valid: bool, error_message: str, params: dict)
    """
    consumer_key = current_app.config['LTI_KEY']
    consumer_secret = current_app.config['LTI_SECRET']

    params = dict(req.form)

    # Check required LTI parameters
    if params.get('oauth_consumer_key') != consumer_key:
        return False, 'Invalid consumer key', params

    if params.get('lti_message_type') != 'basic-lti-launch-request':
        return False, 'Invalid LTI message type', params

    # Check timestamp (allow 5 minute window)
    try:
        timestamp = int(params.get('oauth_timestamp', 0))
        now = int(time.time())
        if abs(now - timestamp) > 3000000:
            return False, 'Request timestamp expired', params
    except (ValueError, TypeError):
        return False, 'Invalid timestamp', params

    # Build the URL that Moodle signed against.
    # Behind a reverse proxy (ngrok / VS Code tunnel / Heroku) the
    # internal req.url is http://127.0.0.1:5000/... but Moodle signed
    # against the *public* HTTPS URL.  We must reconstruct that.
    scheme = req.headers.get('X-Forwarded-Proto',
                             req.headers.get('X-Forwarded-Scheme',
                                             req.scheme))
    host = req.headers.get('X-Forwarded-Host',
                           req.headers.get('Host', req.host))
    # Remove port for standard HTTPS (443) / HTTP (80) — Moodle won't
    # include it in the signed URL.
    if ':' in host:
        h, p = host.rsplit(':', 1)
        if (scheme == 'https' and p == '443') or (scheme == 'http' and p == '80'):
            host = h

    url = f'{scheme}://{host}{req.path}'
    print(f'[LTI DEBUG] Reconstructed URL for signing: {url}')

    # Verify OAuth signature
    base_string = _build_base_string(req.method, url, params)
    expected_signature = _sign(base_string, consumer_secret)

    if params.get('oauth_signature') != expected_signature:
        #print shared key
        print(f"{current_app.config['LTI_KEY']}")
        print(current_app.config['LTI_SECRET'])
        print(params.get('oauth_signature'))
        print(expected_signature)
        return False, 'Invalid OAuth signature', params
    else:
        print("Valid OAuth signature")
    return True, '', params


def extract_lti_user_data(params):
    """Extract user information from LTI launch parameters.

    Returns:
        dict with user_id, name, email, role, outcome data
    """
    # Determine role - Moodle sends roles like 'Instructor', 'Learner',
    # or URN-based roles like 'urn:lti:role:ims/lis/Instructor'
    roles_str = params.get('roles', '')
    is_instructor = any(
        r in roles_str.lower()
        for r in ['instructor', 'administrator', 'teachingassistant',
                   'contentdeveloper', 'mentor']
    )

    return {
        'lti_user_id': params.get('user_id', ''),
        'name': params.get('lis_person_name_full',
                           params.get('lis_person_name_given', 'Unknown')),
        'email': params.get('lis_person_contact_email_primary', ''),
        'role': 'instructor' if is_instructor else 'student',
        'context_id': params.get('context_id', ''),
        'resource_link_id': params.get('resource_link_id', ''),
        'outcome_service_url': params.get('lis_outcome_service_url', ''),
        'result_sourcedid': params.get('lis_result_sourcedid', ''),
    }


def _restore_session_from_token():
    """Try to restore the Flask session from a signed URL token.

    When third-party cookies are blocked (e.g. Chrome incognito in an
    iframe), the cookie session will be empty after the redirect.  The
    LTI launch handler appends a signed ``_lt`` query-param that carries
    the session payload so we can recover it here.

    Returns True if the session was restored, False otherwise.
    """
    token = request.args.get('_lt')
    if not token:
        return False

    from routes.lti_routes import verify_launch_token
    data = verify_launch_token(token, max_age=120)
    if data and 'user_id' in data:
        session['user_id'] = data['user_id']
        session['user_name'] = data['user_name']
        session['role'] = data['role']
        session['lti_session_id'] = data['lti_session_id']
        session.modified = True
        return True
    return False


def require_lti_session(f):
    """Decorator to ensure the user has a valid LTI session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            # Fallback: try to restore from signed URL token
            if not _restore_session_from_token():
                abort(403, description='No active LTI session. Please launch from Moodle.')
        return f(*args, **kwargs)
    return decorated


def require_instructor(f):
    """Decorator to ensure the user is an instructor."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if not _restore_session_from_token():
                abort(403, description='No active LTI session. Please launch from Moodle.')
        if session.get('role') != 'instructor':
            abort(403, description='Instructor access required.')
        return f(*args, **kwargs)
    return decorated

