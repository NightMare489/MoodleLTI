"""
Student Routes.

Problem viewing, code submission, and submission history.
"""

import json
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, session, current_app)
from models.database import db, Problem, TestCase, Submission, LTISession
from lti.auth import require_lti_session
from lti.outcomes import send_grade
from judge.runner import judge_submission

student_bp = Blueprint('student', __name__)


def _token_redirect(endpoint, **kwargs):
    """redirect() that propagates the _lt session token."""
    token = getattr(request, '_session_token', '') or request.args.get('_lt', '')
    if token:
        kwargs['_lt'] = token
    return redirect(url_for(endpoint, **kwargs))


@student_bp.route('/problems')
@require_lti_session
def problem_list():
    """List all active problems."""
    # If the student is locked to a specific problem, redirect them there
    locked = session.get('locked_problem_id')
    if locked:
        return _token_redirect('student.view_problem', problem_id=locked)

    problems = Problem.query.filter_by(is_active=True)\
        .order_by(Problem.created_at.desc()).all()

    # Get best submission for each problem for the current user
    user_id = session['user_id']
    for problem in problems:
        best = Submission.query.filter_by(
            user_id=user_id, problem_id=problem.id
        ).order_by(Submission.score.desc()).first()
        problem.user_best = best

    return render_template('student/problem_list.html', problems=problems)


@student_bp.route('/problem/<int:problem_id>')
@require_lti_session
def view_problem(problem_id):
    """View a problem statement with sample test cases and code editor."""
    # Enforce navigation lock
    locked = session.get('locked_problem_id')
    if locked and int(locked) != problem_id:
        return _token_redirect('student.view_problem', problem_id=locked)

    problem = Problem.query.get_or_404(problem_id)
    if not problem.is_active:
        flash('This problem is not currently available.', 'error')
        return _token_redirect('student.problem_list')

    sample_cases = TestCase.query.filter_by(
        problem_id=problem_id, is_sample=True
    ).order_by(TestCase.order).all()

    languages = current_app.config['SUPPORTED_LANGUAGES']

    # Get user's previous submissions
    user_id = session['user_id']
    submissions = Submission.query.filter_by(
        user_id=user_id, problem_id=problem_id
    ).order_by(Submission.created_at.desc()).limit(10).all()

    return render_template('student/problem.html',
                           problem=problem,
                           sample_cases=sample_cases,
                           languages=languages,
                           submissions=submissions)


@student_bp.route('/problem/<int:problem_id>/submit', methods=['POST'])
@require_lti_session
def submit_code(problem_id):
    """Submit code for judging."""
    problem = Problem.query.get_or_404(problem_id)
    if not problem.is_active:
        flash('This problem is not currently available.', 'error')
        return _token_redirect('student.problem_list')

    code = request.form.get('code', '').strip()
    language = request.form.get('language', 'python')

    if not code:
        flash('Please enter your code.', 'error')
        return _token_redirect('student.view_problem', problem_id=problem_id)

    if language not in current_app.config['SUPPORTED_LANGUAGES']:
        flash('Unsupported language.', 'error')
        return _token_redirect('student.view_problem', problem_id=problem_id)

    # Get all test cases (not just samples)
    test_cases = TestCase.query.filter_by(problem_id=problem_id)\
        .order_by(TestCase.order).all()

    if not test_cases:
        flash('No test cases available for this problem.', 'error')
        return _token_redirect('student.view_problem', problem_id=problem_id)

    # Run the judge
    result = judge_submission(
        code=code,
        language=language,
        test_cases=test_cases,
        time_limit_ms=problem.time_limit_ms,
        memory_limit_mb=problem.memory_limit_mb,
    )

    # Create submission record
    submission = Submission(
        user_id=session['user_id'],
        problem_id=problem_id,
        code=code,
        language=language,
        verdict=result['verdict'],
        score=result['score'],
        results_json=json.dumps(result['results']),
        error_message=result['error'],
    )
    db.session.add(submission)
    db.session.commit()

    # Attempt grade passback to Moodle (binary: 1 = solved, 0 = not)
    lti_session_id = session.get('lti_session_id')
    if lti_session_id:
        lti_sess = LTISession.query.get(lti_session_id)
        if lti_sess and lti_sess.outcome_service_url:
            # Check if the student has ANY accepted submission for this problem
            has_ac = Submission.query.filter_by(
                user_id=session['user_id'],
                problem_id=problem_id,
                verdict='AC'
            ).first() is not None

            grade = 1.0 if has_ac else 0.0

            success, msg = send_grade(
                lti_sess.outcome_service_url,
                lti_sess.result_sourcedid,
                grade
            )
            if not success:
                current_app.logger.warning(f'Grade passback failed: {msg}')

    return _token_redirect('student.view_result', submission_id=submission.id)


@student_bp.route('/submission/<int:submission_id>')
@require_lti_session
def view_result(submission_id):
    """View the result of a submission."""
    submission = Submission.query.get_or_404(submission_id)

    # Enforce navigation lock — can only view results for the locked problem
    locked = session.get('locked_problem_id')
    if locked and submission.problem_id != int(locked):
        return _token_redirect('student.view_problem', problem_id=locked)

    # Only allow viewing own submissions (unless instructor)
    if submission.user_id != session['user_id'] and session.get('role') != 'instructor':
        flash('You do not have permission to view this submission.', 'error')
        return _token_redirect('student.problem_list')

    problem = Problem.query.get(submission.problem_id)

    # Parse results
    try:
        results = json.loads(submission.results_json) if submission.results_json else []
    except json.JSONDecodeError:
        results = []

    # Get sample test cases for display
    sample_cases = TestCase.query.filter_by(
        problem_id=submission.problem_id, is_sample=True
    ).order_by(TestCase.order).all()
    sample_ids = {tc.id for tc in sample_cases}

    return render_template('student/result.html',
                           submission=submission,
                           problem=problem,
                           results=results,
                           sample_ids=sample_ids)


@student_bp.route('/submissions')
@require_lti_session
def my_submissions():
    """View all of the current user's submissions."""
    # If the student is locked to a specific problem, redirect them there
    locked = session.get('locked_problem_id')
    if locked:
        return _token_redirect('student.view_problem', problem_id=locked)

    user_id = session['user_id']
    submissions = Submission.query.filter_by(user_id=user_id)\
        .order_by(Submission.created_at.desc()).all()

    return render_template('student/submissions.html', submissions=submissions)
