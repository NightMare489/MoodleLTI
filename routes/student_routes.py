"""
Student Routes.

Problem viewing, code submission, and submission history.
"""

import json
import threading
from flask import (Blueprint, render_template, request, redirect,
                   url_for, flash, session, current_app)
from sqlalchemy import func, case
from sqlalchemy.orm import joinedload
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

    # Get best submission for each problem in a SINGLE query (avoids N+1)
    user_id = session['user_id']
    problem_ids = [p.id for p in problems]

    if problem_ids:
        best_subs = db.session.query(
            Submission.problem_id,
            func.max(Submission.score).label('best_score'),
            func.max(case((Submission.verdict == 'AC', 1), else_=0)).label('has_ac')
        ).filter(
            Submission.user_id == user_id,
            Submission.problem_id.in_(problem_ids)
        ).group_by(Submission.problem_id).all()

        best_map = {row.problem_id: row for row in best_subs}
    else:
        best_map = {}

    solved_count = 0
    for problem in problems:
        row = best_map.get(problem.id)
        if row:
            # Create a lightweight object for the template
            problem.user_best = type('Best', (), {
                'verdict': 'AC' if row.has_ac else 'WA',
                'score': row.best_score
            })()
            if row.has_ac:
                solved_count += 1
        else:
            problem.user_best = None

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
        return render_template('student/problem_closed.html',
                               problem=problem), 403

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
        return render_template('student/problem_closed.html',
                               problem=problem), 403

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

    # ---- Grade passback to Moodle (non-blocking) ----
    locked_ids, is_single = _get_lock_info()
    lti_session_id = session.get('lti_session_id')
    if lti_session_id:
        lti_sess = LTISession.query.get(lti_session_id)
        if lti_sess and lti_sess.outcome_service_url:
            if locked_ids and len(locked_ids) > 1:
                # Multi-problem sheet: single query instead of loop
                solved = Submission.query.filter(
                    Submission.user_id == session['user_id'],
                    Submission.problem_id.in_(locked_ids),
                    Submission.verdict == 'AC'
                ).with_entities(Submission.problem_id).distinct().count()
                grade = solved / len(locked_ids)
            else:
                # Single problem or no lock: binary 1/0
                has_ac = Submission.query.filter_by(
                    user_id=session['user_id'],
                    problem_id=problem_id,
                    verdict='AC'
                ).first() is not None
                grade = 1.0 if has_ac else 0.0

            # Fire grade passback in a background thread to avoid blocking
            outcome_url = lti_sess.outcome_service_url
            result_sourcedid = lti_sess.result_sourcedid
            app = current_app._get_current_object()

            def _send_grade_bg(app, outcome_url, result_sourcedid, grade):
                with app.app_context():
                    success, msg = send_grade(outcome_url, result_sourcedid, grade)
                    if not success:
                        app.logger.warning(f'Grade passback failed: {msg}')

            threading.Thread(
                target=_send_grade_bg,
                args=(app, outcome_url, result_sourcedid, grade),
                daemon=True
            ).start()

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
        ).options(
            joinedload(Submission.problem)
        ).order_by(Submission.created_at.desc()).limit(50).all()
    else:
        submissions = Submission.query.filter_by(user_id=user_id)\
            .options(joinedload(Submission.problem))\
            .order_by(Submission.created_at.desc()).limit(50).all()

    return render_template('student/submissions.html', submissions=submissions)
