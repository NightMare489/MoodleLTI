"""
Admin/Instructor Routes.

Dashboard for creating/editing problems, managing test cases,
uploading images, and generating test-case output.
"""

import os
import json
import uuid
import tempfile
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, session, current_app, jsonify)
from werkzeug.utils import secure_filename
from models.database import db, Problem, TestCase, Submission, User, ProblemImage
from lti.auth import require_instructor
from judge.runner import compile_code, run_test_case

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'}


def _token_redirect(endpoint, **kwargs):
    """redirect() that propagates the _lt session token."""
    token = getattr(request, '_session_token', '') or request.args.get('_lt', '')
    if token:
        kwargs['_lt'] = token
    return redirect(url_for(endpoint, **kwargs))


def _allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _uploads_dir(problem_id):
    """Return (and create) the upload directory for a problem."""
    base = os.path.join(current_app.root_path, 'static', 'uploads', str(problem_id))
    os.makedirs(base, exist_ok=True)
    return base


# ── Dashboard ────────────────────────────────────────────────────────

@admin_bp.route('/dashboard')
@require_instructor
def dashboard():
    """List all problems."""
    problems = Problem.query.order_by(Problem.created_at.desc()).all()
    return render_template('admin/dashboard.html', problems=problems)


# ── Create / Edit Problem ────────────────────────────────────────────

@admin_bp.route('/problem/new', methods=['GET', 'POST'])
@require_instructor
def new_problem():
    """Create a new problem."""
    if request.method == 'POST':
        problem = Problem(
            title=request.form.get('title', '').strip(),
            description=request.form.get('description', '').strip(),
            time_limit_ms=int(request.form.get('time_limit_ms', 2000)),
            memory_limit_mb=int(request.form.get('memory_limit_mb', 256)),
            solution_code=request.form.get('solution_code', '').strip(),
            solution_language=request.form.get('solution_language', 'c'),
            created_by=session['user_id'],
        )
        db.session.add(problem)
        db.session.commit()
        flash('Problem created successfully!', 'success')
        return _token_redirect('admin.edit_problem', problem_id=problem.id)

    languages = current_app.config['SUPPORTED_LANGUAGES']
    return render_template('admin/problem_form.html', problem=None,
                           languages=languages)


@admin_bp.route('/problem/<int:problem_id>/edit', methods=['GET', 'POST'])
@require_instructor
def edit_problem(problem_id):
    """Edit an existing problem."""
    problem = Problem.query.get_or_404(problem_id)

    if request.method == 'POST':
        problem.title = request.form.get('title', '').strip()
        problem.description = request.form.get('description', '').strip()
        problem.time_limit_ms = int(request.form.get('time_limit_ms', 2000))
        problem.memory_limit_mb = int(request.form.get('memory_limit_mb', 256))
        problem.is_active = 'is_active' in request.form
        problem.solution_code = request.form.get('solution_code', '').strip()
        problem.solution_language = request.form.get('solution_language', 'c')
        db.session.commit()
        flash('Problem updated successfully!', 'success')
        return _token_redirect('admin.edit_problem', problem_id=problem.id)

    test_cases = TestCase.query.filter_by(problem_id=problem.id)\
        .order_by(TestCase.order).all()
    images = ProblemImage.query.filter_by(problem_id=problem.id)\
        .order_by(ProblemImage.created_at).all()
    languages = current_app.config['SUPPORTED_LANGUAGES']
    return render_template('admin/problem_form.html',
                           problem=problem, test_cases=test_cases,
                           images=images, languages=languages)


# ── Image Upload / Delete ────────────────────────────────────────────

@admin_bp.route('/problem/<int:problem_id>/image', methods=['POST'])
@require_instructor
def upload_image(problem_id):
    """Upload an image asset for a problem."""
    problem = Problem.query.get_or_404(problem_id)
    file = request.files.get('image')

    if not file or file.filename == '':
        flash('No file selected.', 'error')
        return _token_redirect('admin.edit_problem', problem_id=problem.id)

    if not _allowed_file(file.filename):
        flash('Invalid file type. Use PNG, JPG, GIF, SVG, or WEBP.', 'error')
        return _token_redirect('admin.edit_problem', problem_id=problem.id)

    # Generate a unique filename to avoid collisions
    ext = file.filename.rsplit('.', 1)[1].lower()
    safe_name = secure_filename(file.filename.rsplit('.', 1)[0])
    unique_name = f"{safe_name}_{uuid.uuid4().hex[:8]}.{ext}"

    dest = os.path.join(_uploads_dir(problem.id), unique_name)
    file.save(dest)

    img = ProblemImage(problem_id=problem.id, filename=unique_name)
    db.session.add(img)
    db.session.commit()

    flash(f'Image uploaded! Use: ![description](image:{unique_name})', 'success')
    return _token_redirect('admin.edit_problem', problem_id=problem.id)


@admin_bp.route('/problem/<int:problem_id>/image/<int:img_id>/delete', methods=['POST'])
@require_instructor
def delete_image(problem_id, img_id):
    """Delete an uploaded image."""
    img = ProblemImage.query.get_or_404(img_id)
    if img.problem_id != problem_id:
        flash('Invalid image.', 'error')
        return _token_redirect('admin.edit_problem', problem_id=problem_id)

    # Remove file from disk
    filepath = os.path.join(_uploads_dir(problem_id), img.filename)
    if os.path.exists(filepath):
        os.remove(filepath)

    db.session.delete(img)
    db.session.commit()
    flash('Image deleted.', 'success')
    return _token_redirect('admin.edit_problem', problem_id=problem_id)


# ── Test Case Management ─────────────────────────────────────────────

@admin_bp.route('/problem/<int:problem_id>/testcase', methods=['POST'])
@require_instructor
def add_test_case(problem_id):
    """Add a test case to a problem."""
    problem = Problem.query.get_or_404(problem_id)

    tc = TestCase(
        problem_id=problem.id,
        input_data=request.form.get('input_data', ''),
        expected_output=request.form.get('expected_output', ''),
        is_sample='is_sample' in request.form,
        order=TestCase.query.filter_by(problem_id=problem.id).count(),
    )
    db.session.add(tc)
    db.session.commit()
    flash('Test case added!', 'success')
    return _token_redirect('admin.edit_problem', problem_id=problem.id)


@admin_bp.route('/problem/<int:problem_id>/testcase/<int:tc_id>/delete', methods=['POST'])
@require_instructor
def delete_test_case(problem_id, tc_id):
    """Delete a test case."""
    tc = TestCase.query.get_or_404(tc_id)
    if tc.problem_id != problem_id:
        flash('Invalid test case.', 'error')
        return _token_redirect('admin.edit_problem', problem_id=problem_id)

    db.session.delete(tc)
    db.session.commit()
    flash('Test case deleted.', 'success')
    return _token_redirect('admin.edit_problem', problem_id=problem_id)


# ── Test Case Output Generator ───────────────────────────────────────

@admin_bp.route('/problem/<int:problem_id>/generate-output', methods=['POST'])
@require_instructor
def generate_output(problem_id):
    """Run the problem's solution code against given input, return output as JSON."""
    problem = Problem.query.get_or_404(problem_id)

    if not problem.solution_code:
        return jsonify({'error': 'No solution code set for this problem.'}), 400

    input_data = request.json.get('input_data', '') if request.is_json else ''
    language = problem.solution_language

    lang_config = current_app.config['SUPPORTED_LANGUAGES'].get(language)
    if not lang_config:
        return jsonify({'error': f'Unsupported language: {language}'}), 400

    work_dir = tempfile.mkdtemp(prefix='gen_')
    try:
        # Compile
        success, exe_path, err = compile_code(problem.solution_code, language, work_dir)
        if not success:
            return jsonify({'error': f'Compilation error:\n{err}'}), 400

        # Run with the given input
        result = run_test_case(
            executable_path=exe_path,
            language=language,
            input_data=input_data,
            expected_output='',       # we don't know expected yet
            test_case_id=0,
            time_limit_s=problem.time_limit_ms / 1000,
            memory_limit_mb=problem.memory_limit_mb,
            work_dir=work_dir,
        )

        if result.verdict == 'RE':
            return jsonify({'error': f'Runtime error:\n{result.error}'}), 400
        if result.verdict == 'TLE':
            return jsonify({'error': 'Time limit exceeded.'}), 400

        return jsonify({'output': result.actual_output})

    finally:
        import shutil
        shutil.rmtree(work_dir, ignore_errors=True)


# ── Batch Test Case Generator ────────────────────────────────────────

@admin_bp.route('/problem/<int:problem_id>/generate-batch', methods=['POST'])
@require_instructor
def generate_batch(problem_id):
    """Split bulk input on blank lines, run each, and save as test cases."""
    import shutil

    problem = Problem.query.get_or_404(problem_id)

    if not problem.solution_code:
        return jsonify({'error': 'No solution code set for this problem.'}), 400

    bulk_input = request.json.get('inputs_bulk', '') if request.is_json else ''
    is_sample = request.json.get('is_sample', False) if request.is_json else False
    language = problem.solution_language

    lang_config = current_app.config['SUPPORTED_LANGUAGES'].get(language)
    if not lang_config:
        return jsonify({'error': f'Unsupported language: {language}'}), 400

    # Split on double-newline (blank line) to get individual test case inputs.
    # Normalize line endings first.
    bulk_input = bulk_input.replace('\r\n', '\n')
    raw_parts = bulk_input.split('\n\n')
    inputs = [p.strip() for p in raw_parts if p.strip()]

    if not inputs:
        return jsonify({'error': 'No test case inputs provided.'}), 400

    work_dir = tempfile.mkdtemp(prefix='gen_batch_')
    try:
        # Compile once
        success, exe_path, err = compile_code(problem.solution_code, language, work_dir)
        if not success:
            return jsonify({'error': f'Compilation error:\n{err}'}), 400

        added = 0
        errors = []
        current_order = TestCase.query.filter_by(problem_id=problem.id).count()

        for idx, inp in enumerate(inputs):
            result = run_test_case(
                executable_path=exe_path,
                language=language,
                input_data=inp,
                expected_output='',
                test_case_id=idx,
                time_limit_s=problem.time_limit_ms / 1000,
                memory_limit_mb=problem.memory_limit_mb,
                work_dir=work_dir,
            )

            if result.verdict in ('RE', 'TLE'):
                err_msg = result.error if result.verdict == 'RE' else 'Time limit exceeded'
                errors.append(f'Test case #{idx + 1}: {err_msg}')
                continue

            tc = TestCase(
                problem_id=problem.id,
                input_data=inp,
                expected_output=result.actual_output,
                is_sample=is_sample,
                order=current_order + added,
            )
            db.session.add(tc)
            added += 1

        db.session.commit()

        resp = {'added': added, 'total': len(inputs)}
        if errors:
            resp['errors'] = errors
        return jsonify(resp)

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ── Submissions & Delete ─────────────────────────────────────────────

@admin_bp.route('/problem/<int:problem_id>/submissions')
@require_instructor
def view_submissions(problem_id):
    """View all submissions for a problem."""
    problem = Problem.query.get_or_404(problem_id)
    submissions = Submission.query.filter_by(problem_id=problem_id)\
        .order_by(Submission.created_at.desc()).all()

    for sub in submissions:
        try:
            sub.parsed_results = json.loads(sub.results_json) if sub.results_json else []
        except json.JSONDecodeError:
            sub.parsed_results = []

    return render_template('admin/submissions.html',
                           problem=problem, submissions=submissions)


@admin_bp.route('/problem/<int:problem_id>/delete', methods=['POST'])
@require_instructor
def delete_problem(problem_id):
    """Delete a problem and all its test cases and submissions."""
    problem = Problem.query.get_or_404(problem_id)
    # Delete submissions first
    Submission.query.filter_by(problem_id=problem_id).delete()
    # Delete test cases (cascade should handle this, but be explicit)
    TestCase.query.filter_by(problem_id=problem_id).delete()
    # Delete images from disk
    import shutil
    uploads = os.path.join(current_app.root_path, 'static', 'uploads', str(problem_id))
    if os.path.exists(uploads):
        shutil.rmtree(uploads, ignore_errors=True)
    ProblemImage.query.filter_by(problem_id=problem_id).delete()
    db.session.delete(problem)
    db.session.commit()
    flash('Problem deleted.', 'success')
    return _token_redirect('admin.dashboard')
