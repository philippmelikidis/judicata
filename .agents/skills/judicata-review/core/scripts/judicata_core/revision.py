"""The revision of an artifact: what exactly a round looked at.

    {"commit": <HEAD SHA or None>, "content": <sha256 hex>, "files": <count>}

content hashes the path and bytes of every file, so it changes when a file is
edited, added, removed or renamed, and never depends on Git. commit is the SHA
of HEAD when the project is in a Git repository with at least one commit, else
None (no git, no repository, no commit yet). Without Git the revision is
therefore the content hash alone. Rounds pass the files of their scope
(scope.Scope.files), so the revision covers exactly what the round looks at.
"""

import hashlib
import subprocess

from .project import Project

CHUNK = 1 << 20


def revision(project: Project, files: list[str]) -> dict:
    """Revision of the given files (POSIX paths relative to the project root)."""
    return {
        "commit": head_commit(project),
        "content": content_hash(project, files),
        "files": len(set(files)),
    }


def content_hash(project: Project, files: list[str]) -> str:
    digest = hashlib.sha256()
    for path in sorted(set(files)):
        digest.update(path.encode("utf-8", "surrogateescape") + b"\0")
        digest.update(_file_digest(project.root / path) + b"\0")
    return digest.hexdigest()


def head_commit(project: Project) -> str | None:
    """SHA of HEAD, or None outside Git, without git or before the first commit."""
    output = _git(project, "rev-parse", "--verify", "--quiet", "HEAD^{commit}")
    if output is None:
        return None
    return output.strip() or None


def _file_digest(path) -> bytes:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK):
                digest.update(chunk)
    except OSError as exc:  # unreadable files still count, as unreadable
        return f"unreadable:{type(exc).__name__}".encode()
    return digest.digest()


def _git(project: Project, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=project.root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None  # no git at all
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", "surrogateescape")
