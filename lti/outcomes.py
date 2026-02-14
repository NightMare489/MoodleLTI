"""
LTI Basic Outcomes Service - Grade Passback to Moodle.

Sends grades (0.0 to 1.0) back to Moodle's gradebook using the
LTI Basic Outcomes POX (Plain Old XML) protocol.
"""

import time
import uuid
import hashlib
import hmac
import base64
import urllib.parse
import requests
from flask import current_app


def _generate_oauth_params(consumer_key):
    """Generate base OAuth parameters for the outcomes request."""
    return {
        'oauth_consumer_key': consumer_key,
        'oauth_signature_method': 'HMAC-SHA1',
        'oauth_timestamp': str(int(time.time())),
        'oauth_nonce': uuid.uuid4().hex,
        'oauth_version': '1.0',
    }


def _build_base_string(method, url, params):
    """Build OAuth signature base string for the outcomes request."""
    sorted_params = sorted(params.items())
    normalized = '&'.join(
        f'{urllib.parse.quote(str(k), safe="")}={urllib.parse.quote(str(v), safe="")}'
        for k, v in sorted_params
    )
    return '&'.join([
        urllib.parse.quote(method.upper(), safe=''),
        urllib.parse.quote(url, safe=''),
        urllib.parse.quote(normalized, safe=''),
    ])


def _sign_request(base_string, consumer_secret):
    """Sign the request with HMAC-SHA1."""
    signing_key = f'{urllib.parse.quote(consumer_secret, safe="")}&'
    hashed = hmac.new(
        signing_key.encode('utf-8'),
        base_string.encode('utf-8'),
        hashlib.sha1
    )
    return base64.b64encode(hashed.digest()).decode('utf-8')


def _build_replace_result_xml(sourcedid, score):
    """Build the replaceResult XML body for grade passback.

    Args:
        sourcedid: The lis_result_sourcedid from the LTI launch
        score: Normalized score between 0.0 and 1.0
    """
    message_id = uuid.uuid4().hex
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<imsx_POXEnvelopeRequest xmlns="http://www.imsglobal.org/services/ltiv1p1/xsd/imsoms_v1p0">
  <imsx_POXHeader>
    <imsx_POXRequestHeaderInfo>
      <imsx_version>V1.0</imsx_version>
      <imsx_messageIdentifier>{message_id}</imsx_messageIdentifier>
    </imsx_POXRequestHeaderInfo>
  </imsx_POXHeader>
  <imsx_POXBody>
    <replaceResultRequest>
      <resultRecord>
        <sourcedGUID>
          <sourcedId>{sourcedid}</sourcedId>
        </sourcedGUID>
        <result>
          <resultScore>
            <language>en</language>
            <textString>{score:.2f}</textString>
          </resultScore>
        </result>
      </resultRecord>
    </replaceResultRequest>
  </imsx_POXBody>
</imsx_POXEnvelopeRequest>'''


def send_grade(outcome_url, sourcedid, score):
    """Send a grade back to Moodle via LTI Basic Outcomes.

    Args:
        outcome_url: The lis_outcome_service_url from LTI launch
        sourcedid: The lis_result_sourcedid from LTI launch
        score: Float between 0.0 and 1.0

    Returns:
        tuple: (success: bool, message: str)
    """
    if not outcome_url or not sourcedid:
        return False, 'No outcome URL or sourcedid available (grade passback not configured)'

    consumer_key = current_app.config['LTI_KEY']
    consumer_secret = current_app.config['LTI_SECRET']

    # Clamp score
    score = max(0.0, min(1.0, float(score)))

    # Build XML body
    xml_body = _build_replace_result_xml(sourcedid, score)

    # Build OAuth parameters
    # For body-hash based signing (LTI outcomes uses body hash)
    body_hash = base64.b64encode(
        hashlib.sha1(xml_body.encode('utf-8')).digest()
    ).decode('utf-8')

    oauth_params = _generate_oauth_params(consumer_key)
    oauth_params['oauth_body_hash'] = body_hash

    # Sign the request
    base_string = _build_base_string('POST', outcome_url, oauth_params)
    signature = _sign_request(base_string, consumer_secret)
    oauth_params['oauth_signature'] = signature

    # Build Authorization header
    auth_header = 'OAuth ' + ', '.join(
        f'{urllib.parse.quote(k, safe="")}="{urllib.parse.quote(v, safe="")}"'
        for k, v in sorted(oauth_params.items())
    )

    # Send the request
    try:
        response = requests.post(
            outcome_url,
            data=xml_body,
            headers={
                'Content-Type': 'application/xml',
                'Authorization': auth_header,
            },
            timeout=10
        )

        if response.status_code == 200 and 'success' in response.text.lower():
            return True, 'Grade sent successfully'
        else:
            return False, f'Moodle returned: {response.status_code} - {response.text[:200]}'

    except requests.RequestException as e:
        return False, f'Failed to send grade: {str(e)}'
