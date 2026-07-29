from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone

db = SQLAlchemy()


def get_current_semester():
    """Retrieve the current active semester from the system settings."""
    try:
        setting = SystemSetting.query.filter_by(key='current_semester').first()
        if setting:
            return setting.value
    except Exception:
        pass
    return 'Summer 2025/2026'


class User(db.Model):
    """Represents a user launched via LTI (student or instructor)."""
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    lti_user_id = db.Column(db.String(255), unique=True, nullable=False)
    regnum = db.Column(db.String(100), unique=True, nullable=True, index=True)
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
    solution_code = db.Column(db.Text, default='')       # correct solution for generator
    solution_language = db.Column(db.String(20), default='c')
    code_template = db.Column(db.Text, default='')       # template with lock markers

    test_cases = db.relationship('TestCase', backref='problem', lazy='dynamic',
                                 cascade='all, delete-orphan')
    images = db.relationship('ProblemImage', backref='problem', lazy='dynamic',
                             cascade='all, delete-orphan')
    submissions = db.relationship('Submission', backref='problem', lazy='dynamic')
    creator = db.relationship('User', backref='created_problems')

    def __repr__(self):
        return f'<Problem {self.title}>'



class ProblemImage(db.Model):
    """An image asset attached to a problem."""
    __tablename__ = 'problem_images'

    id = db.Column(db.Integer, primary_key=True)
    problem_id = db.Column(db.Integer, db.ForeignKey('problems.id'), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<ProblemImage {self.filename} for Problem {self.problem_id}>'


class TestCase(db.Model):
    """A test case for a problem (input → expected output)."""
    __tablename__ = 'test_cases'

    id = db.Column(db.Integer, primary_key=True)
    problem_id = db.Column(db.Integer, db.ForeignKey('problems.id'), nullable=False, index=True)
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
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    problem_id = db.Column(db.Integer, db.ForeignKey('problems.id'), nullable=False, index=True)
    code = db.Column(db.Text, nullable=False)
    language = db.Column(db.String(20), nullable=False)  # 'python', 'c', 'cpp'
    verdict = db.Column(db.String(20), default='PENDING', index=True)  # AC, WA, TLE, RE, CE, PENDING
    semester = db.Column(db.String(50), nullable=False, index=True, default=get_current_semester)

    __table_args__ = (
        db.Index('ix_submission_user_problem', 'user_id', 'problem_id'),
    )
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
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    problem_id = db.Column(db.Integer, db.ForeignKey('problems.id'), nullable=True)
    context_id = db.Column(db.String(255), default='')  # Moodle course ID
    resource_link_id = db.Column(db.String(255), default='')  # Moodle activity ID
    outcome_service_url = db.Column(db.Text, default='')  # For grade passback
    result_sourcedid = db.Column(db.Text, default='')  # For grade passback
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<LTISession user={self.user_id} problem={self.problem_id}>'


class SystemSetting(db.Model):
    """Dynamic system settings (e.g. current active semester)."""
    __tablename__ = 'system_settings'

    key = db.Column(db.String(100), primary_key=True)
    value = db.Column(db.Text, nullable=False)

    def __repr__(self):
        return f'<SystemSetting {self.key}={self.value}>'


class SharedLink(db.Model):
    """A direct access/practice link generated by admin to bypass Moodle."""
    __tablename__ = 'shared_links'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False, index=True)  # e.g. "dAgH31"
    title = db.Column(db.String(255), default='Practice Sheet')
    problem_ids = db.Column(db.Text, nullable=False)  # comma-separated list of problem IDs
    semester = db.Column(db.String(50), nullable=False, index=True, default=get_current_semester)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    screenshare_required = db.Column(db.Boolean, default=False)
    creator = db.relationship('User', backref='created_shared_links')

    def __repr__(self):
        return f'<SharedLink {self.code} ({self.semester})>'


class ProctorSession(db.Model):
    """Tracks active student proctoring sessions."""
    __tablename__ = 'proctor_sessions'

    id = db.Column(db.Integer, primary_key=True)
    session_uuid = db.Column(db.String(64), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    problem_id = db.Column(db.Integer, db.ForeignKey('problems.id'), nullable=True)
    shared_link_code = db.Column(db.String(10), nullable=True)
    status = db.Column(db.String(20), default='ACTIVE', index=True)  # ACTIVE, LOCKED, STOPPED, ENDED
    is_screen_active = db.Column(db.Boolean, default=True)
    paste_count = db.Column(db.Integer, default=0)

    last_seen_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    user = db.relationship('User', backref='proctor_sessions')
    problem = db.relationship('Problem', backref='proctor_sessions')
    events = db.relationship('ProctorEvent', backref='proctor_session', lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<ProctorSession #{self.id} User={self.user_id} Status={self.status}>'


class ProctorEvent(db.Model):
    """Logs anti-cheating audit events (e.g. SCREEN_STOPPED, PASTE_EVENT, LOCK_ACTION)."""
    __tablename__ = 'proctor_events'

    id = db.Column(db.Integer, primary_key=True)
    proctor_session_id = db.Column(db.Integer, db.ForeignKey('proctor_sessions.id'), nullable=False, index=True)
    event_type = db.Column(db.String(50), nullable=False)  # SCREEN_STOPPED, PASTE_EVENT, LOCKED, UNLOCKED
    details = db.Column(db.Text, default='')
    frame_snapshot = db.Column(db.Text, nullable=True)  # Proof snapshot image base64 if applicable
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<ProctorEvent {self.event_type} for Session {self.proctor_session_id}>'

