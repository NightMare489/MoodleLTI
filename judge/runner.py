"""
Code Execution Sandbox & Judge Engine.

Runs student code in isolated subprocesses with time and memory limits.
Compares output against expected test case outputs.
"""

import os
import sys
import json
import shutil
import tempfile
import subprocess
from flask import current_app


class JudgeResult:
    """Result of judging a single test case."""
    def __init__(self, test_case_id, verdict, actual_output='', expected_output='',
                 time_ms=0, error=''):
        self.test_case_id = test_case_id
        self.verdict = verdict  # AC, WA, TLE, RE, CE
        self.actual_output = actual_output[:500]  # Truncate for storage
        self.expected_output = expected_output[:500]
        self.time_ms = time_ms
        self.error = error[:500]

    def to_dict(self):
        return {
            'test_case_id': self.test_case_id,
            'verdict': self.verdict,
            'actual_output': self.actual_output,
            'expected_output': self.expected_output,
            'time_ms': self.time_ms,
            'error': self.error,
        }


def _normalize_output(text):
    """Normalize output for comparison.

    - Strip trailing whitespace from each line
    - Strip trailing newlines
    - Handle Windows/Unix line endings
    """
    lines = text.replace('\r\n', '\n').split('\n')
    # Strip trailing whitespace from each line
    lines = [line.rstrip() for line in lines]
    # Remove trailing empty lines
    while lines and lines[-1] == '':
        lines.pop()
    return '\n'.join(lines)


def _get_resource_limiter_prefix():
    """Get a command prefix to limit resources on Linux (Heroku).

    On Windows (dev), we skip resource limits.
    On Linux (Heroku), we can use ulimit or the resource module.
    """
    if sys.platform == 'win32':
        return []
    # On Linux, we'll handle limits inside the subprocess via preexec_fn
    return []


def _get_preexec_fn(memory_limit_mb):
    """Return a preexec_fn for subprocess to set resource limits on Linux."""
    if sys.platform == 'win32':
        return None

    def set_limits():
        import resource
        # Set memory limit (in bytes)
        mem_bytes = memory_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        # Disable core dumps
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        # Disable file creation beyond 10MB
        resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))

    return set_limits


def compile_code(code, language, work_dir):
    """Compile code if needed.

    Args:
        code: Source code string
        language: Language identifier ('python', 'c', 'cpp')
        work_dir: Working directory for compilation

    Returns:
        tuple: (success: bool, executable_path: str, error: str)
    """
    lang_config = current_app.config['SUPPORTED_LANGUAGES'].get(language)
    if not lang_config:
        return False, '', f'Unsupported language: {language}'

    ext = lang_config['extension']
    source_file = os.path.join(work_dir, f'solution{ext}')

    # Write source code to file
    with open(source_file, 'w', encoding='utf-8') as f:
        f.write(code)

    # If no compilation needed (e.g., Python)
    if lang_config['compile_cmd'] is None:
        return True, source_file, ''

    # Compile
    output_file = os.path.join(work_dir, 'solution')
    if sys.platform == 'win32':
        output_file += '.exe'

    compile_cmd = lang_config['compile_cmd'].format(
        file=source_file, output=output_file
    )

    try:
        compilation_timeout = current_app.config.get('COMPILATION_TIMEOUT', 10)
        result = subprocess.run(
            compile_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=compilation_timeout,
            cwd=work_dir,
        )

        if result.returncode != 0:
            return False, '', result.stderr[:1000]

        return True, output_file, ''

    except subprocess.TimeoutExpired:
        return False, '', 'Compilation timed out'
    except Exception as e:
        return False, '', f'Compilation error: {str(e)}'


def run_test_case(executable_path, language, input_data, expected_output,
                  test_case_id, time_limit_s, memory_limit_mb, work_dir):
    """Run a single test case.

    Args:
        executable_path: Path to the compiled binary or source file
        language: Language identifier
        input_data: Test case input string
        expected_output: Expected output string
        test_case_id: ID of the test case
        time_limit_s: Time limit in seconds
        memory_limit_mb: Memory limit in MB
        work_dir: Working directory

    Returns:
        JudgeResult
    """
    lang_config = current_app.config['SUPPORTED_LANGUAGES'].get(language)
    if not lang_config:
        return JudgeResult(test_case_id, 'CE', error='Unsupported language')

    # Build run command as a list (avoid shell=True for reliable killing)
    run_cmd = lang_config['run_cmd'].format(
        file=executable_path, output=executable_path
    )
    import shlex
    if sys.platform == 'win32':
        cmd_list = run_cmd.split()
    else:
        cmd_list = shlex.split(run_cmd)

    # Add a small buffer for interpreter startup overhead
    effective_timeout = time_limit_s + 1.5

    try:
        import time as time_mod
        import signal

        # Platform-specific process group handling for clean kills
        kwargs = {
            'stdin': subprocess.PIPE,
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE,
            'cwd': work_dir,
        }

        if sys.platform == 'win32':
            kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs['preexec_fn'] = _get_preexec_fn(memory_limit_mb)
            kwargs['start_new_session'] = True

        start_time = time_mod.time()

        proc = subprocess.Popen(cmd_list, **kwargs)

        try:
            stdout, stderr = proc.communicate(
                input=input_data.encode('utf-8') if input_data else None,
                timeout=effective_timeout
            )
        except subprocess.TimeoutExpired:
            # Kill the entire process group
            _kill_process_tree(proc)
            return JudgeResult(
                test_case_id, 'TLE',
                expected_output=expected_output,
                time_ms=time_limit_s * 1000,
                error='Time limit exceeded'
            )

        elapsed_ms = int((time_mod.time() - start_time) * 1000)

        # Also flag as TLE if wall time exceeded the problem's limit
        if elapsed_ms > (time_limit_s * 1000) + 500:
            return JudgeResult(
                test_case_id, 'TLE',
                expected_output=expected_output,
                time_ms=elapsed_ms,
                error='Time limit exceeded'
            )

        stdout_str = stdout.decode('utf-8', errors='replace')
        stderr_str = stderr.decode('utf-8', errors='replace')

        # Check for runtime error
        if proc.returncode != 0:
            return JudgeResult(
                test_case_id, 'RE',
                actual_output=stdout_str,
                expected_output=expected_output,
                time_ms=elapsed_ms,
                error=stderr_str[:500]
            )

        # Compare output
        actual = _normalize_output(stdout_str)
        expected = _normalize_output(expected_output)

        if actual == expected:
            return JudgeResult(
                test_case_id, 'AC',
                actual_output=actual,
                expected_output=expected,
                time_ms=elapsed_ms
            )
        else:
            return JudgeResult(
                test_case_id, 'WA',
                actual_output=actual,
                expected_output=expected,
                time_ms=elapsed_ms
            )

    except MemoryError:
        return JudgeResult(
            test_case_id, 'RE',
            expected_output=expected_output,
            error='Memory limit exceeded'
        )
    except Exception as e:
        return JudgeResult(
            test_case_id, 'RE',
            expected_output=expected_output,
            error=str(e)[:500]
        )


def _kill_process_tree(proc):
    """Kill a process and all its children reliably."""
    try:
        if sys.platform == 'win32':
            # taskkill /F /T kills the process tree on Windows
            subprocess.run(
                ['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                capture_output=True, timeout=5
            )
        else:
            # Kill the entire process group on Linux
            import signal
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def judge_submission(code, language, test_cases, time_limit_ms=2000, memory_limit_mb=256):
    """Judge a complete submission against all test cases.

    Args:
        code: Source code string
        language: Language identifier
        test_cases: List of TestCase model objects
        time_limit_ms: Time limit per test case in milliseconds
        memory_limit_mb: Memory limit in MB

    Returns:
        dict: {
            'verdict': overall verdict,
            'score': float 0.0-1.0,
            'results': list of per-test-case result dicts,
            'error': compilation error if any
        }
    """
    time_limit_s = max(1, time_limit_ms // 1000)
    work_dir = tempfile.mkdtemp(prefix='judge_')

    try:
        # Compile
        success, executable_path, error = compile_code(code, language, work_dir)
        if not success:
            return {
                'verdict': 'CE',
                'score': 0.0,
                'results': [],
                'error': error,
            }

        # Run each test case
        results = []
        passed = 0
        overall_verdict = 'AC'

        for tc in test_cases:
            result = run_test_case(
                executable_path, language,
                tc.input_data, tc.expected_output,
                tc.id, time_limit_s, memory_limit_mb, work_dir
            )
            results.append(result.to_dict())

            if result.verdict == 'AC':
                passed += 1
            elif overall_verdict == 'AC':
                # Set overall verdict to first non-AC verdict
                overall_verdict = result.verdict

        total = len(test_cases)
        score = passed / total if total > 0 else 0.0

        return {
            'verdict': overall_verdict,
            'score': score,
            'results': results,
            'error': '',
        }

    finally:
        # Clean up temp directory
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass
