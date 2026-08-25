"""
Process-tree control for tests that spawn real servers and workers.

Everything a test starts here is a *tree*, not a process. `tsx` on Windows is
`tsx.cmd` -- cmd.exe spawning node.exe -- and `worker.py` shells out to
Ghostscript, pandoc and Typst. `Popen.terminate()` signals only the direct
child, so the descendants outlive it, and they inherited the stdout pipe.

That combination cost a full-suite hang. `api_server` in the integration
conftest terminated tsx and then called a bare `proc.stdout.read()` to report
why the server had not come up; node.exe was still holding the write end, the
pipe never reached EOF, and the read never returned. `./scripts/test.ps1` sat
at 84% forever on any machine without Postgres, leaving orphaned node
processes behind on every run.

Two rules, both enforced here rather than restated at each call site:

  * Kill the tree, never just the child.
  * Read a dead process's output with a deadline. Diagnostics must not outlive
    the thing they are diagnosing.

Deliberately dependency-free (no psutil): psutil is not in requirements.txt,
and a test that guards the suite against hanging must not be the thing that
fails to import.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from urllib.parse import urlparse

DEFAULT_POSTGRES_PORT = 5432

# Returned instead of blocking when a surviving descendant still holds the pipe.
# It says what happened, because a silent "" here reads as "the server printed
# nothing", which is the opposite of the truth and sent us looking in the wrong
# place the first time.
PIPE_STILL_HELD = (
    "<output unavailable: a surviving child process still holds the stdout pipe>"
)


def new_session_kwargs() -> dict:
    """Popen kwargs that make a spawned process reachable as a whole tree.

    POSIX: `start_new_session=True` puts the child in its own process group, so
    `os.killpg` can reach its descendants. Without it `kill_tree` has no group
    to signal but our own -- see the guard in `_kill_tree_posix`.

    Windows: nothing is needed. `taskkill /T` walks the parent/child chain the
    kernel already records.
    """
    return {} if os.name == "nt" else {"start_new_session": True}


def pid_alive(pid: int) -> bool:
    """True if `pid` names a process that has not exited.

    `os.kill(pid, 0)` is the POSIX idiom but is NOT a probe on Windows -- there
    Python maps os.kill onto TerminateProcess, so using it to test liveness
    would kill the process instead of reporting on it.
    """
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102  # still running; WAIT_OBJECT_0 (0) means exited

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _kill_tree_windows(pid: int) -> None:
    subprocess.run(
        ["taskkill", "/T", "/F", "/PID", str(pid)],
        capture_output=True,
        check=False,
        timeout=30,
    )


def _kill_tree_posix(proc: subprocess.Popen) -> None:
    import signal

    try:
        group = os.getpgid(proc.pid)
    except ProcessLookupError:
        return

    # Guard, not politeness: if the child was spawned without
    # new_session_kwargs() it shares OUR process group, and killpg would take
    # down the test runner itself. Fall back to the single child in that case.
    if group == os.getpgid(0):
        proc.terminate()
        return

    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def kill_tree(proc: subprocess.Popen, timeout_s: float = 5.0) -> None:
    """Kill `proc` and every descendant it spawned. Safe on an already-dead process.

    This is what `proc.terminate()` should have been at every call site. The
    difference is not hypothetical: terminating `tsx.cmd` leaves node.exe
    running, and that orphan is what held the pipe open.
    """
    if proc.poll() is not None:
        _close_streams(proc)
        return

    if os.name == "nt":
        _kill_tree_windows(proc.pid)
    else:
        _kill_tree_posix(proc)

    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=timeout_s)
        except (subprocess.TimeoutExpired, OSError):
            pass
    _close_streams(proc)


def _close_streams(proc: subprocess.Popen) -> None:
    """Release our ends of the pipes so the fds do not accumulate across a run."""
    for stream in (proc.stdout, proc.stderr, proc.stdin):
        if stream is not None and not stream.closed:
            try:
                stream.close()
            except OSError:
                pass


def drain_output(proc: subprocess.Popen, timeout_s: float = 5.0) -> str:
    """Read whatever `proc` wrote, with a deadline. Never blocks indefinitely.

    Call `kill_tree` FIRST -- with the tree dead the pipe reaches EOF and this
    returns the full output immediately. The timeout is the backstop for the
    case that made this function necessary: a descendant that outlived the
    child and still holds the write end, where a bare `.read()` never returns.
    """
    if proc.stdout is None:
        return ""
    try:
        out, _ = proc.communicate(timeout=timeout_s)
        return out or ""
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            out, _ = proc.communicate(timeout=timeout_s)
            return out or ""
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return PIPE_STILL_HELD


def kill_tree_and_drain(proc: subprocess.Popen, timeout_s: float = 5.0) -> str:
    """Kill the whole tree, then return its output. The correct order, made hard
    to get wrong -- getting it wrong is the entire bug this module exists for."""
    captured = ""
    if proc.stdout is not None:
        if os.name == "nt":
            _kill_tree_windows(proc.pid)
        else:
            _kill_tree_posix(proc)
        captured = drain_output(proc, timeout_s=timeout_s)
    kill_tree(proc, timeout_s=timeout_s)
    return captured


def postgres_reachable(url: str, timeout_s: float = 1.0) -> bool:
    """Cheap TCP probe of the host:port in a libpq URL.

    Deliberately not a psycopg2 connect: this runs before every integration
    test session, including on machines that have no database at all, and it
    must answer in milliseconds without raising. It answers "is something
    listening", which is exactly the question that decides skip-vs-run --
    anything subtler (wrong password, missing schema) should still fail loudly
    inside the tests rather than silently skip them.
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or DEFAULT_POSTGRES_PORT
    except ValueError:
        return False

    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def postgres_skip_reason(url: str, timeout_s: float = 1.0) -> str | None:
    """None if Postgres is up, else the message to skip with."""
    if postgres_reachable(url, timeout_s=timeout_s):
        return None
    parsed = urlparse(url)
    where = f"{parsed.hostname or '127.0.0.1'}:{parsed.port or DEFAULT_POSTGRES_PORT}"
    return (
        f"Postgres is not listening on {where} -- the integration tests need a real "
        f"database. Start it with `docker compose up -d postgres` (the host port is "
        f"55432, not 5432), or point DATABASE_URL at your own instance."
    )


__all__ = [
    "PIPE_STILL_HELD",
    "drain_output",
    "kill_tree",
    "kill_tree_and_drain",
    "new_session_kwargs",
    "pid_alive",
    "postgres_reachable",
    "postgres_skip_reason",
]
