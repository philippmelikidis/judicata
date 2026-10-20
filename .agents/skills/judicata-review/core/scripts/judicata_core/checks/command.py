"""Command: a shell command in the project directory, exit code 0 passes.

Parameters: command (string), timeout (seconds, default 300). stdout and stderr
are merged; only the last 64 KiB are kept while reading, and the last lines go
into the ledger as evidence.
"""

import math
import os
import signal
import subprocess
import threading
import time

DEFAULT_TIMEOUT = 300
TAIL_LINES = 20
TAIL_CHARS = 4000
TAIL_BYTES = 64 * 1024
READER_GRACE = 5  # seconds to wait for output after the command ended
# Why a command criterion has no result when a round closes: round check did
# not run it, usually because the commands wait for approval (see approvals.py).
PENDING_REASON = (
    "not run in this round: round check runs commands only after their approval"
)


def validate(params: dict) -> tuple[dict, list[tuple[str, str]]]:
    problems = [
        (key, "unknown field") for key in sorted(set(params) - {"command", "timeout"})
    ]
    command = params.get("command")
    if not isinstance(command, str) or not command.strip():
        problems.append(("command", "required, a non-empty shell command"))
    timeout = params.get("timeout", DEFAULT_TIMEOUT)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, int | float)
        or not _finite(timeout)
        or timeout <= 0
    ):
        problems.append(("timeout", "must be a positive number of seconds"))
    return {"command": command, "timeout": timeout}, problems


def _finite(value: float) -> bool:
    try:
        return math.isfinite(value)
    except OverflowError:  # an int beyond float range
        return False


def run(project, criterion, scope) -> dict:
    command = criterion.params["command"]
    timeout = criterion.params["timeout"]
    started = time.monotonic()
    proc = subprocess.Popen(
        command,
        shell=True,
        cwd=project.root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    tail = bytearray()
    reader = threading.Thread(target=_keep_tail, args=(proc.stdout, tail), daemon=True)
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc)
        proc.wait()
    reader.join(timeout=READER_GRACE)
    text = bytes(tail).decode("utf-8", errors="replace")
    if timed_out:
        text += f"\n[judicata] timed out after {timeout} s, command was stopped\n"
    return {
        "passed": not timed_out and proc.returncode == 0,
        "evidence": {
            "command": command,
            "exit_code": None if timed_out else proc.returncode,
            "timed_out": timed_out,
            "duration_seconds": round(time.monotonic() - started, 3),
            "output_tail": _tail(text),
        },
    }


def _keep_tail(stream, tail: bytearray) -> None:
    """Read the output to the end but keep only its last TAIL_BYTES in memory."""
    while chunk := stream.read1(65536):
        tail += chunk
        if len(tail) > TAIL_BYTES:
            del tail[:-TAIL_BYTES]


def _kill_tree(proc: subprocess.Popen) -> None:
    """Stop the shell and everything it started (new session = own group)."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass


def _tail(text: str) -> str:
    lines = text.rstrip("\n").splitlines()
    tail = "\n".join(lines[-TAIL_LINES:])
    return tail[-TAIL_CHARS:]
