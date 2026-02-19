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


def _get_lock_info():
    """Return (locked_ids, is_single) from session.

    locked_ids: list of ints or None
    is_single:  True when locked to exactly one problem
    """
    ids = session.get('locked_problem_ids')
    if ids:
        return ids, len(ids) == 1
    return None, False


@student_bp.route('/problems')
@require_lti_session
def problem_list():
    """List all active problems (or only sheet problems)."""
    locked_ids, is_single = _get_lock_info()

    # Single-problem lock: redirect straight to the problem
    if is_single:
        return _token_redirect('student.view_problem', problem_id=locked_ids[0])

    # Multi-problem sheet: filter to sheet problems only
    if locked_ids:
        problems = Problem.query.filter(
            Problem.id.in_(locked_ids),
            Problem.is_active == True
        ).order_by(Problem.id.asc()).all()
    else:
        # No lock — show all active problems
        problems = Problem.query.filter_by(is_active=True)\
            .order_by(Problem.created_at.desc()).all()

    # Get best submission for each problem for the current user
    user_id = session['user_id']
    solved_count = 0
    for problem in problems:
        best = Submission.query.filter_by(
            user_id=user_id, problem_id=problem.id
        ).order_by(Submission.score.desc()).first()
        problem.user_best = best
        if best and best.verdict == 'AC':
            solved_count += 1

    return render_template('student/problem_list.html',
                           problems=problems,
                           is_sheet=bool(locked_ids and len(locked_ids) > 1),
                           solved_count=solved_count,
                           total_count=len(locked_ids) if locked_ids else 0)


@student_bp.route('/problem/<int:problem_id>')
@require_lti_session
def view_problem(problem_id):
    """View a problem statement with sample test cases and code editor."""
    locked_ids, is_single = _get_lock_info()

    # Enforce navigation lock
    if locked_ids and problem_id not in locked_ids:
        if is_single:
            return _token_redirect('student.view_problem', problem_id=locked_ids[0])
        else:
            return _token_redirect('student.problem_list')

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

    # ---- Grade passback to Moodle ----
    locked_ids, is_single = _get_lock_info()
    lti_session_id = session.get('lti_session_id')
    if lti_session_id:
        lti_sess = LTISession.query.get(lti_session_id)
        if lti_sess and lti_sess.outcome_service_url:
            if locked_ids and len(locked_ids) > 1:
                # Multi-problem sheet:
                # grade = solved_count / total_problems
                # Each AC problem = 1 point. Moodle total = len(locked_ids).
                solved = 0
                for pid in locked_ids:
                    has_ac = Submission.query.filter_by(
                        user_id=session['user_id'],
                        problem_id=pid,
                        verdict='AC'
                    ).first()
                    if has_ac:
                        solved += 1
                grade = solved / len(locked_ids)
            else:
                # Single problem or no lock: binary 1/0
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

    # Enforce navigation lock — can only view results for allowed problems
    locked_ids, is_single = _get_lock_info()
    if locked_ids and submission.problem_id not in locked_ids:
        if is_single:
            return _token_redirect('student.view_problem', problem_id=locked_ids[0])
        else:
            return _token_redirect('student.problem_list')

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
    locked_ids, is_single = _get_lock_info()

    # Single-problem lock: redirect to the problem
    if is_single:
        return _token_redirect('student.view_problem', problem_id=locked_ids[0])

    user_id = session['user_id']

    if locked_ids:
        # Sheet mode: show only submissions for sheet problems
        submissions = Submission.query.filter(
            Submission.user_id == user_id,
            Submission.problem_id.in_(locked_ids)
        ).order_by(Submission.created_at.desc()).all()
    else:
        submissions = Submission.query.filter_by(user_id=user_id)\
            .order_by(Submission.created_at.desc()).all()

    return render_template('student/submissions.html', submissions=submissions)
