"""
Proctoring Routes.

Handles screen capture frame streaming, telemetry events, and admin live proctoring dashboard.
"""

from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, request, jsonify, session, current_app
from models.database import db, User, Problem, SharedLink, ProctorSession, ProctorEvent
from lti.auth import require_lti_session

import time
import requests

proctor_bp = Blueprint('proctor', __name__)

# Cache for dynamic Cloudflare TURN credentials (cache for 12 hours)
CF_TURN_CACHE = {'servers': None, 'expires_at': 0}


@proctor_bp.route('/proctor/ice-config', methods=['GET'])
def get_ice_config():
    """Return ICE server configuration (STUN + TURN) for WebRTC connections."""
    cf_key_id = current_app.config.get('CLOUDFLARE_TURN_KEY_ID', '') or current_app.config.get('TURN_SERVER_USERNAME', '')
    cf_api_token = current_app.config.get('CLOUDFLARE_API_TOKEN', '') or current_app.config.get('TURN_SERVER_CREDENTIAL', '')
    turn_url = current_app.config.get('TURN_SERVER_URL', '')

    # 1. Dynamic Cloudflare Realtime TURN Generation
    if cf_key_id and cf_api_token and not turn_url:
        now = time.time()
        if CF_TURN_CACHE['servers'] and now < CF_TURN_CACHE['expires_at']:
            return jsonify({
                'iceServers': CF_TURN_CACHE['servers'],
                'iceTransportPolicy': 'relay'
            })

        try:
            resp = requests.post(
                f"https://rtc.live.cloudflare.com/v1/turn/keys/{cf_key_id}/credentials/generate-ice-servers",
                headers={
                    'Authorization': f"Bearer {cf_api_token}",
                    'Content-Type': 'application/json'
                },
                json={'ttl': 86400},
                timeout=5
            )
            if resp.status_code == 201:
                data = resp.json()
                if 'iceServers' in data:
                    CF_TURN_CACHE['servers'] = data['iceServers']
                    CF_TURN_CACHE['expires_at'] = now + 43200  # Cache for 12 hours
                    return jsonify({
                        'iceServers': data['iceServers'],
                        'iceTransportPolicy': 'relay'
                    })
        except Exception as e:
            current_app.logger.warning(f"Failed to fetch dynamic Cloudflare TURN credentials: {e}")

    # 2. Static Cloudflare TURN Fallback Config
    turn_user = current_app.config.get('TURN_SERVER_USERNAME', '')
    turn_cred = current_app.config.get('TURN_SERVER_CREDENTIAL', '')

    ice_servers = [
        # {'urls': 'stun:stun.cloudflare.com:3478'}
    ]

    res = {
        'iceServers': ice_servers,
        'iceTransportPolicy': 'relay'
    }

    if turn_url and turn_user:
        turn_urls = [u.strip() for u in turn_url.split(',') if u.strip()]
        ice_servers.append({
            'urls': turn_urls if len(turn_urls) > 1 else turn_urls[0],
            'username': turn_user,
            'credential': turn_cred
        })

    return jsonify(res)


@proctor_bp.route('/proctor/heartbeat', methods=['POST'])
def proctor_heartbeat():
    """Receive student heartbeat ping and telemetry events."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    user_id = session['user_id']
    data = request.get_json() or {}

    session_uuid = data.get('session_uuid')
    if not session_uuid:
        return jsonify({'error': 'Missing session_uuid'}), 400

    problem_id = data.get('problem_id')
    shared_link_code = data.get('shared_link_code')
    event_type = data.get('event_type')
    details = data.get('details', '')

    now = datetime.now(timezone.utc)

    # Find or create ProctorSession
    proc_sess = ProctorSession.query.filter_by(session_uuid=session_uuid).first()
    if not proc_sess:
        proc_sess = ProctorSession(
            session_uuid=session_uuid,
            user_id=user_id,
            problem_id=problem_id if problem_id else None,
            shared_link_code=shared_link_code if shared_link_code else None,
            status='ACTIVE',
            is_screen_active=True,
            last_seen_at=now
        )
        db.session.add(proc_sess)
        db.session.flush()

    # Update heartbeat
    proc_sess.last_seen_at = now
    proc_sess.is_screen_active = True
    if proc_sess.status == 'STOPPED':
        proc_sess.status = 'ACTIVE'

    # Process events
    if event_type:
        if event_type == 'SCREEN_STOPPED':
            proc_sess.is_screen_active = False
            proc_sess.status = 'STOPPED'

        event = ProctorEvent(
            proctor_session_id=proc_sess.id,
            event_type=event_type,
            details=details
        )
        db.session.add(event)

    db.session.commit()

    return jsonify({
        'status': proc_sess.status,
        'is_locked': proc_sess.status == 'LOCKED',
        'server_time': now.isoformat()
    })


@proctor_bp.route('/admin/proctor')
@require_lti_session
def admin_proctor_dashboard():
    """Render the Admin Live Proctoring Dashboard."""
    if session.get('role') != 'instructor':
        return render_template('error.html', error='Instructor access required.'), 403

    return render_template('admin/proctor_dashboard.html')


@proctor_bp.route('/admin/proctor/sessions', methods=['GET'])
@require_lti_session
def admin_get_active_sessions():
    """JSON API endpoint returning active proctoring sessions for live grid."""
    if session.get('role') != 'instructor':
        return jsonify({'error': 'Unauthorized'}), 403

    # Consider active if seen in last 10 minutes
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    sessions = ProctorSession.query.filter(ProctorSession.last_seen_at >= cutoff)\
        .order_by(ProctorSession.last_seen_at.desc()).all()

    now = datetime.now(timezone.utc)
    res = []
    for s in sessions:
        user = s.user
        problem = s.problem
        # Check if heartbeat missed for > 6 seconds
        is_stale = (now - s.last_seen_at.replace(tzinfo=timezone.utc if s.last_seen_at.tzinfo is None else s.last_seen_at)).total_seconds() > 6

        # Fetch recent 3 events
        recent_events = [{
            'event_type': e.event_type,
            'details': e.details,
            'created_at': e.created_at.strftime('%H:%M:%S') if e.created_at else ''
        } for e in s.events.order_by(ProctorEvent.created_at.desc()).limit(3).all()]

        res.append({
            'session_id': s.id,
            'session_uuid': s.session_uuid,
            'student_name': user.name if user else 'Unknown',
            'regnum': user.regnum if user else 'N/A',
            'problem_title': problem.title if problem else (f"Sheet {s.shared_link_code}" if s.shared_link_code else "Exam"),
            'status': s.status if not is_stale else 'OFFLINE',
            'is_screen_active': s.is_screen_active and not is_stale,
            'last_seen_seconds_ago': int((now - s.last_seen_at.replace(tzinfo=timezone.utc if s.last_seen_at.tzinfo is None else s.last_seen_at)).total_seconds()),
            'recent_events': recent_events
        })

    return jsonify({'sessions': res, 'server_time': now.isoformat()})


@proctor_bp.route('/admin/proctor/toggle_lock', methods=['POST'])
@require_lti_session
def admin_toggle_lock_session():
    """Lock or unlock a student exam session."""
    if session.get('role') != 'instructor':
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json() or {}
    session_id = data.get('session_id')
    proc_sess = ProctorSession.query.get(session_id)

    if not proc_sess:
        return jsonify({'error': 'Session not found'}), 404

    if proc_sess.status == 'LOCKED':
        proc_sess.status = 'ACTIVE'
        evt_type = 'UNLOCKED'
    else:
        proc_sess.status = 'LOCKED'
        evt_type = 'LOCKED'

    event = ProctorEvent(
        proctor_session_id=proc_sess.id,
        event_type=evt_type,
        details=f"Status changed to {proc_sess.status} by admin"
    )
    db.session.add(event)
    db.session.commit()

    return jsonify({'success': True, 'new_status': proc_sess.status})



# ---------------------------------------------------------------------------
# WebRTC Signaling Hub (In-Memory Queue for SDP / ICE Candidate Exchange)
# ---------------------------------------------------------------------------
WEBRTC_SIGNALS = {}  # key: (session_uuid, recipient) -> list of signals


@proctor_bp.route('/proctor/webrtc/signal', methods=['POST'])
def send_webrtc_signal():
    """Receive an SDP offer/answer or ICE candidate and queue for recipient."""
    data = request.get_json() or {}
    session_uuid = data.get('session_uuid')
    recipient = data.get('recipient')  # 'admin' or 'student'
    signal_type = data.get('type')      # 'offer', 'answer', 'candidate'
    payload = data.get('payload')

    if not session_uuid or not recipient or not payload:
        return jsonify({'error': 'Invalid signal parameters'}), 400

    key = f"{session_uuid}:{recipient}"
    if key not in WEBRTC_SIGNALS:
        WEBRTC_SIGNALS[key] = []

    # Add signal to recipient's queue
    WEBRTC_SIGNALS[key].append({
        'type': signal_type,
        'payload': payload,
        'timestamp': datetime.now(timezone.utc).timestamp()
    })

    # Limit queue size to 50 items max
    if len(WEBRTC_SIGNALS[key]) > 50:
        WEBRTC_SIGNALS[key] = WEBRTC_SIGNALS[key][-50:]

    return jsonify({'success': True})


@proctor_bp.route('/proctor/webrtc/poll', methods=['GET'])
def poll_webrtc_signals():
    """Poll and consume pending WebRTC signaling messages for a session recipient."""
    session_uuid = request.args.get('session_uuid')
    recipient = request.args.get('recipient')  # 'admin' or 'student'

    if not session_uuid or not recipient:
        return jsonify({'error': 'Missing parameters'}), 400

    key = f"{session_uuid}:{recipient}"
    signals = WEBRTC_SIGNALS.pop(key, [])

    return jsonify({'signals': signals})

