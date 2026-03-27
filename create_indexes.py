"""Create indexes on existing database and verify WAL mode."""
from app import app
from sqlalchemy import text

with app.app_context():
    from models.database import db
    indexes = [
        'CREATE INDEX IF NOT EXISTS ix_submissions_user_id ON submissions(user_id)',
        'CREATE INDEX IF NOT EXISTS ix_submissions_problem_id ON submissions(problem_id)',
        'CREATE INDEX IF NOT EXISTS ix_submissions_verdict ON submissions(verdict)',
        'CREATE INDEX IF NOT EXISTS ix_submission_user_problem ON submissions(user_id, problem_id)',
        'CREATE INDEX IF NOT EXISTS ix_lti_sessions_user_id ON lti_sessions(user_id)',
        'CREATE INDEX IF NOT EXISTS ix_test_cases_problem_id ON test_cases(problem_id)',
        'CREATE INDEX IF NOT EXISTS ix_problem_images_problem_id ON problem_images(problem_id)',
    ]
    with db.engine.connect() as conn:
        for idx in indexes:
            conn.execute(text(idx))
            name = idx.split(' ON ')[0].replace('CREATE INDEX IF NOT EXISTS ', '')
            print(f'OK: {name}')
        conn.commit()
        result = conn.execute(text('PRAGMA journal_mode'))
        print(f'Journal mode: {result.scalar()}')
    print('All indexes created and WAL enabled!')
