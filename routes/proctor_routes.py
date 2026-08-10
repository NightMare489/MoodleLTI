"""
Proctoring Routes.

Handles screen capture stream publishing/subscribing via Cloudflare Calls (SFU),
telemetry events, and instructor live proctoring dashboard.
"""

from datetime import datetime, timezone, timedelta
from flask import Blueprint, render_template, request, jsonify, session, current_app
from models.database import db, User, Problem, SharedLink, ProctorSession, ProctorEvent
from lti.auth import require_lti_session

import time
import requests

proctor_bp = Blueprint('proctor', __name__)

CLOUDFLARE_CALLS_API_BASE = "https://rtc.live.cloudflare.com/v1"


def get_cf_calls_config():
    """Retrieve Cloudflare Calls SFU App ID and API Token from config."""
    app_id = current_app.config.get('CLOUDFLARE_CALLS_APP_ID', '').strip()
    token = current_app.config.get('CLOUDFLARE_CALLS_API_TOKEN', '').strip()
    return app_id, token


@proctor_bp.route('/proctor/ice-config', methods=['GET'])
def get_ice_config():
    """Return ICE server configuration (Cloudflare STUN / TURN) for WebRTC connections."""
    return jsonify({
        'iceServers': [
            {'urls': 'stun:stun.cloudflare.com:3478'}
        ],
        'iceTransportPolicy': 'all'
    })


# ---------------------------------------------------------------------------
# Cloudflare Calls SFU Endpoints
# ---------------------------------------------------------------------------

@proctor_bp.route('/proctor/sfu/publish', methods=['POST'])
def sfu_publish_track():
    """
    Student screen publisher endpoint.
    Creates a session on Cloudflare Calls SFU and publishes the screen video track.
    """
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    app_id, token = get_cf_calls_config()
    if not app_id or not token:
        return jsonify({'error': 'Cloudflare Calls SFU is not configured on server'}), 500

    data = request.get_json() or {}
    session_uuid = data.get('session_uuid')
    sdp_offer = data.get('sdp')
    mid = str(data.get('mid', '0'))
    track_name = data.get('track_name', 'screen')

    if not session_uuid or not sdp_offer:
        return jsonify({'error': 'Missing session_uuid or sdp offer'}), 400

    headers = {
        'Authorization': f'Bearer {token}'
    }

    try:
        # 1. Create a new Session in Cloudflare Calls
        sess_resp = requests.post(
            f"{CLOUDFLARE_CALLS_API_BASE}/apps/{app_id}/sessions/new",
            headers=headers,
            timeout=10
        )
        if sess_resp.status_code != 201:
            current_app.logger.error(f"Cloudflare Calls new session failed: {sess_resp.status_code} {sess_resp.text}")
            return jsonify({'error': 'Failed to create SFU session on Cloudflare', 'details': sess_resp.text}), 502

        cf_session_id = sess_resp.json().get('sessionId')

        # 2. Add the local track with the student's SDP offer
        track_resp = requests.post(
            f"{CLOUDFLARE_CALLS_API_BASE}/apps/{app_id}/sessions/{cf_session_id}/tracks/new",
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            },
            json={
                'sessionDescription': {
                    'sdp': sdp_offer,
                    'type': 'offer'
                },
                'tracks': [
                    {
                        'location': 'local',
                        'mid': mid,
                        'trackName': track_name
                    }
                ]
            },
            timeout=10
        )

        if track_resp.status_code != 200:
            current_app.logger.error(f"Cloudflare Calls add track failed: {track_resp.status_code} {track_resp.text}")
            return jsonify({'error': 'Failed to register local track on SFU', 'details': track_resp.text}), 502

        track_data = track_resp.json()
        answer_desc = track_data.get('sessionDescription')

        # 3. Update or create ProctorSession record in database
        user_id = session['user_id']
        now = datetime.now(timezone.utc)
        proc_sess = ProctorSession.query.filter_by(session_uuid=session_uuid).first()
        if not proc_sess:
            proc_sess = ProctorSession(
                session_uuid=session_uuid,
                user_id=user_id,
                status='ACTIVE',
                is_screen_active=True,
                cf_session_id=cf_session_id,
                cf_track_name=track_name,
                last_seen_at=now
            )
            db.session.add(proc_sess)
        else:
            proc_sess.cf_session_id = cf_session_id
            proc_sess.cf_track_name = track_name
            proc_sess.is_screen_active = True
            proc_sess.last_seen_at = now
            if proc_sess.status == 'STOPPED':
                proc_sess.status = 'ACTIVE'

        db.session.commit()

        return jsonify({
            'success': True,
            'cf_session_id': cf_session_id,
            'cf_track_name': track_name,
            'sessionDescription': answer_desc
        })

    except Exception as e:
        current_app.logger.exception(f"Error publishing track to Cloudflare Calls: {e}")
        return jsonify({'error': str(e)}), 500


@proctor_bp.route('/admin/proctor/sfu/subscribe', methods=['POST'])
@require_lti_session
def sfu_subscribe_track():
    """
    Instructor/Proctor subscriber endpoint.
    Creates a new subscriber session on Cloudflare Calls SFU and pulls the student's screen track.
    Returns Cloudflare's SDP offer for the subscriber.
    """
    if session.get('role') != 'instructor':
        return jsonify({'error': 'Unauthorized'}), 403

    app_id, token = get_cf_calls_config()
    if not app_id or not token:
        return jsonify({'error': 'Cloudflare Calls SFU is not configured on server'}), 500

    data = request.get_json() or {}
    session_uuid = data.get('session_uuid')
    cf_session_id = data.get('cf_session_id')
    cf_track_name = data.get('cf_track_name', 'screen')

    if not cf_session_id and session_uuid:
        proc_sess = ProctorSession.query.filter_by(session_uuid=session_uuid).first()
        if proc_sess:
            cf_session_id = proc_sess.cf_session_id
            cf_track_name = proc_sess.cf_track_name or 'screen'

    if not cf_session_id:
        return jsonify({'error': 'Student track not available or screen sharing not active'}), 404

    headers = {
        'Authorization': f'Bearer {token}'
    }

    try:
        # 1. Create a new subscriber session in Cloudflare Calls for this viewer
        sess_resp = requests.post(
            f"{CLOUDFLARE_CALLS_API_BASE}/apps/{app_id}/sessions/new",
            headers=headers,
            timeout=10
        )
        if sess_resp.status_code != 201:
            current_app.logger.error(f"Cloudflare Calls new subscriber session failed: {sess_resp.status_code} {sess_resp.text}")
            return jsonify({'error': 'Failed to create SFU subscriber session', 'details': sess_resp.text}), 502

        admin_sub_session_id = sess_resp.json().get('sessionId')

        # 2. Pull the remote track from the student's publisher session
        track_resp = requests.post(
            f"{CLOUDFLARE_CALLS_API_BASE}/apps/{app_id}/sessions/{admin_sub_session_id}/tracks/new",
            headers={
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json'
            },
            json={
                'tracks': [
                    {
                        'location': 'remote',
                        'sessionId': cf_session_id,
                        'trackName': cf_track_name
                    }
                ]
            },
            timeout=10
        )

        if track_resp.status_code != 200:
            current_app.logger.error(f"Cloudflare Calls pull track failed: {track_resp.status_code} {track_resp.text}")
            return jsonify({'error': 'Failed to pull track from SFU', 'details': track_resp.text}), 502

        track_data = track_resp.json()
        offer_desc = track_data.get('sessionDescription')

        return jsonify({
            'success': True,
            'admin_session_id': admin_sub_session_id,
            'sessionDescription': offer_desc,
            'tracks': track_data.get('tracks', [])
        })

    except Exception as e:
        current_app.logger.exception(f"Error subscribing to track via Cloudflare Calls: {e}")
        return jsonify({'error': str(e)}), 500


@proctor_bp.route('/admin/proctor/sfu/renegotiate', methods=['POST'])
@require_lti_session
def sfu_renegotiate_track():
    """
    Submits the instructor's SDP answer back to Cloudflare Calls to finalize subscription.
    """
    if session.get('role') != 'instructor':
        return jsonify({'error': 'Unauthorized'}), 403

    app_id, token = get_cf_calls_config()
    if not app_id or not token:
        return jsonify({'error': 'Cloudflare Calls SFU is not configured on server'}), 500

    data = request.get_json() or {}
    admin_session_id = data.get('admin_session_id')
    session_description = data.get('sessionDescription')

    if not admin_session_id or not session_description:
        return jsonify({'error': 'Missing admin_session_id or sessionDescription'}), 400

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }

    try:
        resp = requests.put(
            f"{CLOUDFLARE_CALLS_API_BASE}/apps/{app_id}/sessions/{admin_session_id}/renegotiate",
            headers=headers,
            json={
                'sessionDescription': session_description
            },
            timeout=10
        )

        if resp.status_code != 200:
            current_app.logger.error(f"Cloudflare Calls renegotiate failed: {resp.status_code} {resp.text}")
            return jsonify({'error': 'Failed to renegotiate SFU session', 'details': resp.text}), 502

        return jsonify({'success': True})

    except Exception as e:
        current_app.logger.exception(f"Error renegotiating SFU session: {e}")
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Telemetry & Dashboard Management
# ---------------------------------------------------------------------------

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
            proc_sess.cf_session_id = None

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
            'cf_session_id': s.cf_session_id if (s.is_screen_active and not is_stale) else None,
            'cf_track_name': s.cf_track_name or 'screen',
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

