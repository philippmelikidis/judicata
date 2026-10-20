"""The project the core works on, and the only places it writes to.

Everything lives under <project>/.judicata/ (contracts/<name>.toml, ledger.jsonl),
plus one line in <project>/.gitattributes so Git merges the ledger by union.
"""

from pathlib import Path

from .errors import JudicataError

STATE_DIR = ".judicata"
GITATTRIBUTES_PATTERN = f"{STATE_DIR}/ledger.jsonl"
GITATTRIBUTES_LINE = f"{GITATTRIBUTES_PATTERN} merge=union"


class Project:
    def __init__(self, root: str):
        path = Path(root).expanduser()
        if not path.is_dir():
            raise JudicataError(
                "project_not_found",
                f"project directory does not exist: {root}",
                {"project": root},
            )
        self.root = path.resolve()

    @property
    def state_dir(self) -> Path:
        return self.root / STATE_DIR

    @property
    def contracts_dir(self) -> Path:
        return self.state_dir / "contracts"

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "ledger.jsonl"

    def prepare_for_writing(self) -> None:
        """Create .judicata/ and make sure Git merges the ledger by union."""
        self.state_dir.mkdir(exist_ok=True)
        ensure_gitattributes(self.root / ".gitattributes")


def ensure_gitattributes(path: Path) -> None:
    """Add the union merge line unless it is already in effect. Never rewrites lines.

    In effect means: our line exists and no later line sets a merge attribute
    (a later line could override it; matching globs is not worth the risk).
    """
    data = path.read_bytes() if path.exists() else b""
    text = data.decode("utf-8", errors="replace")
    in_effect = False
    for line in text.splitlines():
        fields = line.split()
        if not fields or fields[0].startswith("#"):
            continue
        if fields[0].lstrip("/") == GITATTRIBUTES_PATTERN and "merge=union" in fields:
            in_effect = True
        elif any(f.lstrip("-!").split("=")[0] == "merge" for f in fields[1:]):
            in_effect = False
    if in_effect:
        return
    newline = b"\r\n" if b"\r\n" in data else b"\n"
    addition = b""
    if data and not data.endswith(b"\n"):
        addition += newline
    addition += GITATTRIBUTES_LINE.encode("utf-8") + newline
    with path.open("ab") as handle:
        handle.write(addition)
