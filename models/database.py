from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


class User(db.Model):
    """Represents a user launched via LTI (student or instructor)."""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    lti_user_id = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255), default='Unknown')
    email = db.Column(db.String(255), default='')
    role = db.Column(db.String(50), default='student')  # 'student' or 'instructor'
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    submissions = db.relationship('Submission', backref='user', lazy='dynamic')
    lti_sessions = db.relationship('LTISession', backref='user', lazy='dynamic')

    def __repr__(self):
        return f'<User {self.name} ({self.role})>'


class Problem(db.Model):
    """A programming problem created by an instructor."""
    __tablename__ = 'problems'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)  # Markdown supported
    time_limit_ms = db.Column(db.Integer, default=2000)  # milliseconds
    memory_limit_mb = db.Column(db.Integer, default=256)  # megabytes
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_active = db.Column(db.Boolean, default=True)

    test_cases = db.relationship('TestCase', backref='problem', lazy='dynamic',
                                 cascade='all, delete-orphan')
    submissions = db.relationship('Submission', backref='problem', lazy='dynamic')
    creator = db.relationship('User', backref='created_problems')

    def __repr__(self):
        return f'<Problem {self.title}>'


class TestCase(db.Model):
    """A test case for a problem (input → expected output)."""
    __tablename__ = 'test_cases'

    id = db.Column(db.Integer, primary_key=True)
    problem_id = db.Column(db.Integer, db.ForeignKey('problems.id'), nullable=False)
    input_data = db.Column(db.Text, nullable=False)
    expected_output = db.Column(db.Text, nullable=False)
    is_sample = db.Column(db.Boolean, default=False)  # Visible to students
    order = db.Column(db.Integer, default=0)

    def __repr__(self):
        return f'<TestCase #{self.id} for Problem {self.problem_id}>'


class Submission(db.Model):
    """A code submission by a student."""
    __tablename__ = 'submissions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    problem_id = db.Column(db.Integer, db.ForeignKey('problems.id'), nullable=False)
    code = db.Column(db.Text, nullable=False)
    language = db.Column(db.String(20), nullable=False)  # 'python', 'c', 'cpp'
    verdict = db.Column(db.String(20), default='PENDING')  # AC, WA, TLE, RE, CE, PENDING
    score = db.Column(db.Float, default=0.0)  # 0.0 to 1.0
    results_json = db.Column(db.Text, default='[]')  # Per-test-case results as JSON
    error_message = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<Submission #{self.id} {self.verdict}>'


class LTISession(db.Model):
    """Stores LTI launch data needed for grade passback."""
    __tablename__ = 'lti_sessions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    problem_id = db.Column(db.Integer, db.ForeignKey('problems.id'), nullable=True)
    context_id = db.Column(db.String(255), default='')  # Moodle course ID
    resource_link_id = db.Column(db.String(255), default='')  # Moodle activity ID
    outcome_service_url = db.Column(db.Text, default='')  # For grade passback
    result_sourcedid = db.Column(db.Text, default='')  # For grade passback
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<LTISession user={self.user_id} problem={self.problem_id}>'
