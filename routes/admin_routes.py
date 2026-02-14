"""
Admin/Instructor Routes.

Dashboard for creating/editing problems, managing test cases,
and viewing student submissions.
"""

import json
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from models.database import db, Problem, TestCase, Submission, User
from lti.auth import require_instructor

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def _token_redirect(endpoint, **kwargs):
    """redirect() that propagates the _lt session token."""
    token = getattr(request, '_session_token', '') or request.args.get('_lt', '')
    if token:
        kwargs['_lt'] = token
    return redirect(url_for(endpoint, **kwargs))


@admin_bp.route('/dashboard')
@require_instructor
def dashboard():
    """List all problems."""
    problems = Problem.query.order_by(Problem.created_at.desc()).all()
    return render_template('admin/dashboard.html', problems=problems)


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
            created_by=session['user_id'],
        )
        db.session.add(problem)
        db.session.commit()
        flash('Problem created successfully!', 'success')
        return _token_redirect('admin.edit_problem', problem_id=problem.id)

    return render_template('admin/problem_form.html', problem=None)


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
        db.session.commit()
        flash('Problem updated successfully!', 'success')
        return _token_redirect('admin.edit_problem', problem_id=problem.id)

    test_cases = TestCase.query.filter_by(problem_id=problem.id)\
        .order_by(TestCase.order).all()
    return render_template('admin/problem_form.html',
                           problem=problem, test_cases=test_cases)


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


@admin_bp.route('/problem/<int:problem_id>/submissions')
@require_instructor
def view_submissions(problem_id):
    """View all submissions for a problem."""
    problem = Problem.query.get_or_404(problem_id)
    submissions = Submission.query.filter_by(problem_id=problem_id)\
        .order_by(Submission.created_at.desc()).all()

    # Parse results JSON for each submission
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
    db.session.delete(problem)
    db.session.commit()
    flash('Problem deleted.', 'success')
    return _token_redirect('admin.dashboard')
