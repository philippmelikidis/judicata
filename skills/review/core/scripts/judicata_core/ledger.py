"""The ledger: an append-only JSONL file, one event per line.

Event: {"id": <uuid4 hex>, "ts": <UTC ISO 8601 with Z>, "type": <snake_case>, "data": {...}}
Lines are only ever appended. Reading drops repeated ids (Git union merges can
duplicate lines) and returns the events in replay order: by timestamp, then id.
A union merge puts the lines of two branches in either order; sorting makes the
state the same whichever branch merged the other. Each new event gets a
timestamp later than every event already in the file (even when the clock is
behind a merged branch), so one file never replays in a different order than
it was written.
"""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta

TICK = timedelta(microseconds=1)
# judicata writes uuid4 hex ids and snake_case types; anything else is not ours
# and must not carry text into messages (untrusted.py)
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
TYPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

from .errors import JudicataError
from .project import Project


def append(project: Project, event_type: str, data: dict) -> dict:
    return append_many(project, [(event_type, data)])[0]


def append_many(project: Project, items: list[tuple[str, dict]]) -> list[dict]:
    """Append several events with one write, so they land together or not at all."""
    project.prepare_for_writing()
    path = project.ledger_path
    stamp = _latest_timestamp(path)
    events = []
    for event_type, data in items:
        now = datetime.now(UTC)
        stamp = now if stamp is None or now > stamp else _next(path, stamp)
        events.append(
            {
                "id": uuid.uuid4().hex,
                "ts": _format(stamp),
                "type": event_type,
                "data": data,
            }
        )
    text = "".join(
        json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        for event in events
    )
    if path.exists() and path.stat().st_size > 0:
        with path.open("rb") as existing:
            existing.seek(-1, 2)
            if existing.read(1) != b"\n":
                text = "\n" + text  # never glue onto a hand-edited last line
    with path.open("ab") as handle:
        handle.write(text.encode("utf-8"))
    return events


def read(project: Project) -> list[dict]:
    """All events in replay order (timestamp, then id), each id once."""
    path = project.ledger_path
    if not path.exists():
        return []
    events: list[dict] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise _corrupt(path, number, f"not valid JSON ({exc.msg})") from None
            if not (
                isinstance(event, dict)
                and isinstance(event.get("id"), str)
                and isinstance(event.get("type"), str)
                and isinstance(event.get("data"), dict)
            ):
                raise _corrupt(path, number, "event needs id, type and data")
            if not ID_RE.match(event["id"]) or not TYPE_RE.match(event["type"]):
                raise _corrupt(
                    path,
                    number,
                    "event id or type has characters judicata never writes",
                )
            stamp = _parse(event.get("ts"))
            if stamp is None:
                raise _corrupt(path, number, "event needs a UTC timestamp in ts")
            if event["id"] in seen:
                continue
            seen.add(event["id"])
            events.append((stamp, event["id"], event))
    events.sort(key=lambda item: item[:2])
    return [event for _, _, event in events]


def _parse(value) -> datetime | None:
    """The timestamp as an aware UTC datetime, None if it is not one."""
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            return stamp.replace(tzinfo=UTC)
        return stamp.astimezone(UTC)
    except (ValueError, OverflowError):  # OverflowError: beyond year 9999 in UTC
        return None


def _next(path, stamp: datetime) -> datetime:
    try:
        return stamp + TICK
    except OverflowError:
        raise JudicataError(
            "ledger_corrupt",
            "the ledger holds a timestamp at the end of time, no event can follow it",
            {"file": str(path)},
        ) from None


def _format(stamp: datetime) -> str:
    return stamp.astimezone(UTC).isoformat(timespec="microseconds")[:-6] + "Z"


def _latest_timestamp(path) -> datetime | None:
    """The latest ts in the file; lines that do not parse are skipped here."""
    if not path.exists():
        return None
    latest = None
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            stamp = _parse(event.get("ts")) if isinstance(event, dict) else None
            if stamp is not None and (latest is None or stamp > latest):
                latest = stamp
    return latest


def _corrupt(path, number: int, problem: str) -> JudicataError:
    return JudicataError(
        "ledger_corrupt",
        f"ledger line {number} is unreadable: {problem}",
        {"file": str(path), "line": number},
    )
