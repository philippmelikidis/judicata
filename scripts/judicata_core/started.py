"""When a session started, for the stop gate when it has no transcript.

The stop gate (gate.py) takes the session start from the first timestamp of
the transcript. Some sessions have none (claude -p --no-session-persistence),
so the SessionStart hook also leaves a marker in the user's state directory
(approvals.state_dir, outside the project):

  <state dir>/sessions/<key>.json   {"started": UTC ISO 8601}

key = sha256 of the project root and the session id. The first SessionStart
of a session writes it; later ones (resume, clear, compact) keep it. Markers
older than KEEP_DAYS are removed when a new one is written. Every error is
ignored: without a marker the gate only has the transcript.

The gate keeps its count of blocks per session under the same key in
<state dir>/gate/ (path() with folder "gate", see gate.py).
"""

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from . import approvals
from .project import Project

KEEP_DAYS = 30
MAX_ID = 200


def record(root: Path, session_id) -> None:
    """Remember the start of this session in this project (first call wins)."""
    marker = path(root, session_id)
    if marker is None or marker.exists():
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    prune(marker.parent)
    now = datetime.now(UTC).isoformat(timespec="microseconds")
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"started": now}, handle)


def recalled(root: Path, session_id) -> float | None:
    """The recorded start of this session (epoch seconds), or None."""
    marker = path(root, session_id)
    if marker is None or not marker.is_file():
        return None
    try:
        value = json.loads(marker.read_text(encoding="utf-8")).get("started")
        return datetime.fromisoformat(value).timestamp()
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def path(root: Path, session_id, folder: str = "sessions") -> Path | None:
    """<state dir>/<folder>/<key>.json for this session in this project, None
    without a usable session id. The stop gate counts its blocks in "gate"."""
    if not isinstance(session_id, str) or not session_id or len(session_id) > MAX_ID:
        return None
    key = json.dumps([str(root), session_id], ensure_ascii=False)
    name = hashlib.sha256(key.encode("utf-8", "surrogateescape")).hexdigest()
    return approvals.state_dir(Project(str(root)), None) / folder / f"{name}.json"


def prune(folder: Path) -> None:
    limit = time.time() - KEEP_DAYS * 86_400
    for item in folder.glob("*.json"):
        try:
            if item.stat().st_mtime < limit:
                item.unlink()
        except OSError:
            continue
