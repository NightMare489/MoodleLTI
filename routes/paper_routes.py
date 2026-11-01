"""
Exam Paper Viewer (separate from the judge).

A Moodle LTI activity with the custom parameter ``paper_set=<set_name>``
launches here instead of the judge.  The student is redirected straight to
their own pages of the scanned exam, served as a bare PDF so the browser's
built-in viewer fills the iframe with no portal UI around it.

Files on the server (outside static/, never served directly):
    PAPERS_DIR/<set_name>/exam.pdf     the full scanned PDF
    PAPERS_DIR/<set_name>/marks.xlsx   sheet with "PDF Page" + "Reg. Number" columns

Security model
--------------
* The full PDF is never sent.  For each request the server copies only the
  student's pages into a new in-memory PDF and sends that.
* Which pages a student gets is decided only by the reg number Moodle put in
  the OAuth-signed launch.  The view URL carries a signed, short-lived token
  holding that reg number; there is no reg-number parameter a student can
  edit, so changing the URL cannot reveal another student's pages.
* Only instructors (role from the signed launch) can pick a reg number.
"""

import io
import os
import re
import threading

from flask import Blueprint, abort, current_app, make_response, redirect, request, send_file, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from markupsafe import escape

paper_bp = Blueprint('papers', __name__, url_prefix='/papers')

_SAFE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')
_SALT = 'paper-view'
_PDF_NAME = 'exam.pdf'
_MARKS_NAME = 'marks.xlsx'

# Per-set cache: {set_name: {'stamp':..., 'reader':..., 'pages': {reg: [idx,...]}, 'out': {reg: bytes}}}
_cache = {}
_lock = threading.Lock()


class PaperSetError(Exception):
    """The set's files are missing or inconsistent (shown to instructors only)."""


def _serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=_SALT)


def _load_page_map(xlsx_path, total_pages, per_student):
    """Map reg number -> list of 0-based page indexes, with sanity checks
    so a shifted row can never hand one student another student's page."""
    import openpyxl

    ws = openpyxl.load_workbook(xlsx_path, data_only=True, read_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    for h, row in enumerate(rows[:20]):
        names = [str(v).strip() if v is not None else '' for v in row]
        if 'PDF Page' in names and 'Reg. Number' in names:
            pc, rc = names.index('PDF Page'), names.index('Reg. Number')
            break
    else:
        raise PaperSetError("marks.xlsx has no header row with 'PDF Page' and 'Reg. Number'.")

    pages, used = {}, {}
    for row in rows[h + 1:]:
        if len(row) <= max(pc, rc):
            continue
        start, reg = row[pc], row[rc]
        if not isinstance(start, (int, float)) or reg is None:
            continue  # absent students, summary rows, blanks
        start, reg = int(start), str(reg).strip()
        if not _SAFE.match(reg):
            raise PaperSetError(f'Invalid reg number {reg!r}.')
        if reg in pages:
            raise PaperSetError(f'Reg number {reg} appears twice.')
        idx = list(range(start - 1, start - 1 + per_student))
        if idx[0] < 0 or idx[-1] >= total_pages:
            raise PaperSetError(f'{reg}: pages {start}-{start + per_student - 1} are outside the PDF.')
        for i in idx:
            if i in used:
                raise PaperSetError(f'PDF page {i + 1} is assigned to both {used[i]} and {reg}.')
            used[i] = reg
        pages[reg] = idx
    return pages


def _get_set(set_name):
    """Load (and cache until the files change) the reader and page map."""
    from pypdf import PdfReader

    folder = os.path.join(current_app.config['PAPERS_DIR'], set_name)
    pdf_path = os.path.join(folder, _PDF_NAME)
    xlsx_path = os.path.join(folder, _MARKS_NAME)
    if not (os.path.isfile(pdf_path) and os.path.isfile(xlsx_path)):
        raise PaperSetError(f'Missing {_PDF_NAME} or {_MARKS_NAME} in {set_name}/.')
    stamp = (os.path.getmtime(pdf_path), os.path.getmtime(xlsx_path))

    entry = _cache.get(set_name)
    if entry and entry['stamp'] == stamp:
        return entry
    reader = PdfReader(pdf_path)
    per = current_app.config['PAPER_PAGES_PER_STUDENT']
    entry = {
        'stamp': stamp,
        'reader': reader,
        'pages': _load_page_map(xlsx_path, len(reader.pages), per),
        'out': {},
    }
    _cache[set_name] = entry
    return entry


def _student_pdf(set_name, regnum):
    """Bytes of a new PDF holding only this student's pages, or None."""
    if not (_SAFE.match(set_name or '') and _SAFE.match(regnum or '')):
        return None
    from pypdf import PdfWriter

    with _lock:  # pypdf readers are not thread-safe
        entry = _get_set(set_name)
        idx = entry['pages'].get(regnum)
        if idx is None:
            return None
        if regnum not in entry['out']:
            w = PdfWriter()
            for i in idx:
                w.add_page(entry['reader'].pages[i])
            w.add_metadata({'/Title': f'Paper {regnum}'})
            buf = io.BytesIO()
            w.write(buf)
            entry['out'][regnum] = buf.getvalue()
        return entry['out'][regnum]


def _plain_page(message, status=200):
    html = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Exam paper</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        '<body style="font-family:system-ui,sans-serif;margin:2rem;color:#333">'
        f'{message}</body></html>'
    )
    return html, status, {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}


def _candidate_regnums(params):
    """Reg numbers Moodle may have sent (all covered by the OAuth signature)."""
    out = []
    for key in ('lis_person_sourcedid', 'ext_user_username', 'custom_regnum'):
        v = (params.get(key) or '').strip()
        if v and v not in out:
            out.append(v)
    return out


def handle_paper_launch(params, user_data):
    """Called from the LTI launch after the OAuth signature is verified."""
    set_name = (params.get('custom_paper_set') or current_app.config['PAPERS_DEFAULT_SET']).strip()
    if not _SAFE.match(set_name):
        return _plain_page('<p>This activity is misconfigured (invalid paper_set).</p>', 400)

    s = _serializer()
    if user_data['role'] == 'instructor':
        token = s.dumps({'set': set_name, 'role': 'instructor'})
        return redirect(url_for('papers.instructor_index', t=token), code=303)

    try:
        with _lock:
            known = _get_set(set_name)['pages']
    except PaperSetError:
        current_app.logger.exception('Paper set %s is not usable', set_name)
        return _plain_page('<p>Exam papers are not available yet. Please try again later.</p>', 503)

    for reg in _candidate_regnums(params):
        if reg in known:
            token = s.dumps({'set': set_name, 'reg': reg, 'role': 'student'})
            return redirect(url_for('papers.view', t=token), code=303)

    current_app.logger.info('Paper launch: no paper for %s in set %s',
                            _candidate_regnums(params), set_name)
    return _plain_page('<p>No exam paper was found for your account.</p>'
                       '<p>If you sat this exam, please contact your lecturer.</p>', 404)


def _load_token():
    try:
        return _serializer().loads(request.args.get('t', ''),
                                   max_age=current_app.config['PAPER_LINK_MAX_AGE'])
    except SignatureExpired:
        abort(make_response(*_plain_page('<p>This link has expired. Please open the activity again from Moodle.</p>', 403)))
    except BadSignature:
        abort(make_response(*_plain_page('<p>Invalid link. Please open the activity from Moodle.</p>', 403)))


@paper_bp.route('/view')
def view():
    """Stream the student's pages inline so the browser's own viewer shows them."""
    data = _load_token()
    if data.get('role') == 'instructor':
        reg = request.args.get('reg', '')  # instructors may choose
    else:
        reg = data.get('reg', '')          # students: only the signed value
    try:
        pdf = _student_pdf(data.get('set', ''), reg)
    except PaperSetError:
        current_app.logger.exception('Paper set error')
        abort(503)
    if pdf is None:
        abort(404)

    resp = send_file(io.BytesIO(pdf), mimetype='application/pdf', conditional=False,
                     download_name=f'paper_{reg}.pdf', as_attachment=False, max_age=0)
    resp.headers['Cache-Control'] = 'private, no-store'
    resp.headers['Referrer-Policy'] = 'no-referrer'
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Robots-Tag'] = 'noindex, nofollow'
    return resp


@paper_bp.route('/instructor')
def instructor_index():
    """Minimal list of every paper in the set, for instructors only."""
    data = _load_token()
    if data.get('role') != 'instructor':
        abort(403)
    set_name = data.get('set', '')
    try:
        with _lock:
            regs = sorted(_get_set(set_name)['pages'])
    except PaperSetError as e:
        return _plain_page(f'<p><b>Paper set "{escape(set_name)}" problem:</b> {escape(str(e))}</p>', 503)
    t = request.args.get('t', '')
    items = ''.join(
        f'<li><a href="{escape(url_for("papers.view", t=t, reg=r))}" target="paper">{escape(r)}</a></li>'
        for r in regs
    )
    return _plain_page(
        f'<h3 style="margin-top:0">Papers: {escape(set_name)} ({len(regs)})</h3>'
        '<div style="display:flex;gap:1rem;height:85vh">'
        f'<ul style="overflow:auto;min-width:9rem;margin:0;padding-left:1.2rem">{items}</ul>'
        '<iframe name="paper" style="flex:1;border:1px solid #ccc"></iframe></div>'
    )
