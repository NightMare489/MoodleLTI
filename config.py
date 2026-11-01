import os
import sys

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

try:
    from dotenv import load_dotenv
    dotenv_path = os.path.join(BASE_DIR, '.env')
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path)
except ImportError:
    pass


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
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 280,
    }

    # Session cookie settings — required for LTI in iframes.
    # Moodle embeds the tool in an iframe from a different domain,
    # so the cookie must be SameSite=None + Secure to survive cross-site.
    SESSION_COOKIE_SAMESITE = 'None'
    SESSION_COOKIE_SECURE = True       # Required when SameSite=None
    SESSION_COOKIE_HTTPONLY = True

    # LTI Configuration
    LTI_KEY = os.environ.get('LTI_KEY', 'moodle-judge-key')
    LTI_SECRET = os.environ.get('LTI_SECRET', 'moodle-judge-secret')

    # Exam paper viewer (separate from the judge).  Each set is a folder
    # PAPERS_DIR/<set_name>/ holding exam.pdf (full scan) and marks.xlsx
    # ("PDF Page" + "Reg. Number" columns).  Keep it outside static/: students
    # only ever receive their own pages via /papers/view.
    PAPERS_DIR = os.environ.get('PAPERS_DIR', os.path.join(BASE_DIR, 'papers_private'))
    PAPERS_DEFAULT_SET = os.environ.get('PAPERS_DEFAULT_SET', 'ccs1101_quiz1')
    PAPER_PAGES_PER_STUDENT = int(os.environ.get('PAPER_PAGES_PER_STUDENT', '2'))
    PAPER_LINK_MAX_AGE = int(os.environ.get('PAPER_LINK_MAX_AGE', '7200'))  # seconds

    # Grade proxy – set to your Google Apps Script web-app URL
    GRADE_PROXY_URL = os.environ.get('GRADE_PROXY_URL', 'https://script.google.com/macros/s/AKfycbx0ZepXCbDFzFNjYS-TqdC9K8-iyxexF4soU7UwDa9Jm3SkFjYqgCv_f1inhOS1d9CIHQ/exec')

    # Judge Configuration
    EXECUTION_TIMEOUT = int(os.environ.get('EXECUTION_TIMEOUT', '5'))  # seconds
    COMPILATION_TIMEOUT = int(os.environ.get('COMPILATION_TIMEOUT', '10'))  # seconds
    MAX_OUTPUT_SIZE = int(os.environ.get('MAX_OUTPUT_SIZE', '1048576'))  # 1MB

    # Set JUDGE_JAVA_HOME to use a JDK that isn't on PATH (e.g. one unpacked in ~).
    # Not JAVA_HOME: hosts like PythonAnywhere preset that to a JRE without javac.
    _JAVA_BIN = os.path.join(os.environ['JUDGE_JAVA_HOME'], 'bin', '') if os.environ.get('JUDGE_JAVA_HOME') else ''

    # Supported languages
    SUPPORTED_LANGUAGES = {
        'c': {
            'name': 'C (GCC)',
            'extension': '.c',
            'compile_cmd': 'gcc -o {output} {file} -lm -Dscanf_s=scanf',
            'run_cmd': '{output}',
        },
        'java': {
            'name': 'Java (OpenJDK)',
            'extension': '.java',
            # -J-XX:TieredStopAtLevel=1 makes javac start noticeably faster on slow shared CPUs
            'compile_cmd': _JAVA_BIN + 'javac -encoding UTF-8 -J-Xmx256m -J-XX:TieredStopAtLevel=1 -d . {file}',
            # Runs in the work dir; {main_class} is detected from the source.
            'run_cmd': _JAVA_BIN + 'java -Xmx{memory_mb}m -Xss64m -XX:+UseSerialGC -cp . {main_class}',
            # The JVM reserves far more virtual memory than it uses, so an
            # RLIMIT_AS cap kills it at startup; -Xmx bounds the heap instead.
            'rlimit_as': False,
            'extra_time_ms': 1000,  # JVM startup allowance
        },
    }

    # WebRTC TURN Server (Static or Cloudflare Realtime API)
    TURN_SERVER_URL = os.environ.get('TURN_SERVER_URL', '')
    TURN_SERVER_USERNAME = os.environ.get('TURN_SERVER_USERNAME', '')
    TURN_SERVER_CREDENTIAL = os.environ.get('TURN_SERVER_CREDENTIAL', '')
    CLOUDFLARE_TURN_KEY_ID = os.environ.get('CLOUDFLARE_TURN_KEY_ID', '')
    CLOUDFLARE_API_TOKEN = os.environ.get('CLOUDFLARE_API_TOKEN', '')
    CLOUDFLARE_TURN_PROXY_URL = os.environ.get('CLOUDFLARE_TURN_PROXY_URL', '')

    # Cloudflare Calls (Realtime SFU)
    CLOUDFLARE_CALLS_APP_ID = os.environ.get('CLOUDFLARE_CALLS_APP_ID', '')
    CLOUDFLARE_CALLS_API_TOKEN = os.environ.get('CLOUDFLARE_CALLS_API_TOKEN', '')
    CLOUDFLARE_CALLS_PROXY_URL = os.environ.get('CLOUDFLARE_CALLS_PROXY_URL', '')

