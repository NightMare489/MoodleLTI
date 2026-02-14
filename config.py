import os
import sys


BASE_DIR = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Base configuration."""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-secret-change-me-in-production')

    # Database – absolute path so it works on PythonAnywhere
    DATABASE_URL = os.environ.get(
        'DATABASE_URL',
        'sqlite:///' + os.path.join(BASE_DIR, 'judge.db')
    )
    # Heroku uses 'postgres://' but SQLAlchemy requires 'postgresql://'
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Session cookie settings — required for LTI in iframes.
    # Moodle embeds the tool in an iframe from a different domain,
    # so the cookie must be SameSite=None + Secure to survive cross-site.
    SESSION_COOKIE_SAMESITE = 'None'
    SESSION_COOKIE_SECURE = True       # Required when SameSite=None
    SESSION_COOKIE_HTTPONLY = True

    # LTI Configuration
    LTI_KEY = os.environ.get('LTI_KEY', 'moodle-judge-key')
    LTI_SECRET = os.environ.get('LTI_SECRET', 'moodle-judge-secret')

    # Grade proxy – set to your Google Apps Script web-app URL
    GRADE_PROXY_URL = os.environ.get('GRADE_PROXY_URL', 'https://script.google.com/macros/s/AKfycbx0ZepXCbDFzFNjYS-TqdC9K8-iyxexF4soU7UwDa9Jm3SkFjYqgCv_f1inhOS1d9CIHQ/exec')

    # Judge Configuration
    EXECUTION_TIMEOUT = int(os.environ.get('EXECUTION_TIMEOUT', '5'))  # seconds
    COMPILATION_TIMEOUT = int(os.environ.get('COMPILATION_TIMEOUT', '10'))  # seconds
    MAX_OUTPUT_SIZE = int(os.environ.get('MAX_OUTPUT_SIZE', '1048576'))  # 1MB

    # Supported languages
    SUPPORTED_LANGUAGES = {
        'python': {
            'name': 'Python 3',
            'extension': '.py',
            'compile_cmd': None,
            'run_cmd': 'python3 {file}',
        },
        'c': {
            'name': 'C (GCC)',
            'extension': '.c',
            'compile_cmd': 'gcc -o {output} {file} -lm',
            'run_cmd': '{output}',
        },
        'cpp': {
            'name': 'C++ (G++)',
            'extension': '.cpp',
            'compile_cmd': 'g++ -o {output} {file} -lm',
            'run_cmd': '{output}',
        },
    }
