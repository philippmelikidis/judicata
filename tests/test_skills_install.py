"""judicata as plain Agent Skills (npx skills): the skills ship their own core.

An install copies only the skill folders (skills/<name>/), so the review skill
carries a copy of the core in skills/review/core/, built from scripts/,
templates/ and agents/ by tools/bundle_core.py. These tests keep
the copy equal to the source and run the core from an installed copy.
"""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import proposal

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "bundle_core.py"
SKILLS = REPO / "skills"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")
# Fields of the Agent Skills specification (agentskills.io) that judicata uses.
FRONTMATTER_FIELDS = {"name", "description", "license", "compatibility", "metadata"}


def bundle(*args: str, root: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--root", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_the_bundled_core_matches_the_source():
    result = bundle("--check")

    assert result.returncode == 0, result.stdout + result.stderr


def copy_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for part in ("scripts", "templates", "agents", "skills"):
        shutil.copytree(REPO / part, root / part, ignore=IGNORE)
    shutil.copy(REPO / "LICENSE", root / "LICENSE")
    return root


def test_a_drifted_bundle_is_reported_and_rebuilt(tmp_path):
    root = copy_repo(tmp_path)
    core = root / "skills" / "review" / "core"
    (core / "scripts" / "judicata_core" / "ledger.py").write_text("# by hand\n")
    (core / "templates" / "stale.toml").write_text("")
    (root / "templates" / "letter.toml").write_text('name = "letter"\n')
    entry = core / "scripts" / "judicata.py"
    entry.chmod(0o644)  # the source entry point is executable (shebang)

    checked = bundle("--check", root=root)

    assert checked.returncode == 1
    for path in (
        "judicata_core/ledger.py",
        "templates/stale.toml",
        "letter.toml",
        "scripts/judicata.py",
    ):
        assert path in checked.stdout
    assert bundle(root=root).returncode == 0
    assert bundle("--check", root=root).returncode == 0
    assert not (core / "templates" / "stale.toml").exists()
    assert entry.stat().st_mode & 0o111
    for path in ("scripts/judicata_core/ledger.py", "templates/letter.toml"):
        assert (core / path).read_bytes() == (root / path).read_bytes()


def test_a_missing_source_fails_and_keeps_the_bundle(tmp_path):
    root = copy_repo(tmp_path)
    (root / "scripts" / "judicata.py").unlink()
    entry = root / "skills" / "review" / "core" / "scripts" / "judicata.py"

    checked = bundle("--check", root=root)
    built = bundle(root=root)

    for result in (checked, built):
        assert result.returncode != 0
        assert "scripts/judicata.py" in result.stdout + result.stderr
    assert entry.is_file()


@pytest.fixture
def installed(project, tmp_path) -> Path:
    """The skill folders alone, copied as npx skills does, without the plugin."""
    skills = tmp_path / "host" / ".agents" / "skills"
    shutil.copytree(SKILLS, skills, ignore=IGNORE)
    project.script = skills / "review" / "core" / "scripts" / "judicata.py"
    return skills


def test_installed_skills_run_the_core_without_the_plugin(project, installed):
    templates = project.ok("template", "list", "--for", ".")["templates"]
    assert {t["name"] for t in templates} == {"code", "docs", "prose"}

    project.ok("contract", "create", stdin=proposal(project, "prose"))
    contracts = project.ok("status")["contracts"]
    summary = project.ok("session", "summary")

    assert [c["contract"] for c in contracts] == ["prose"]
    assert summary["found"] is True
    assert str(project.script) in summary["context"]
    assert (installed / "review" / "core" / "agents" / "reviewer.md").is_file()
    assert (installed / "review" / "core" / "agents" / "verifier.md").is_file()


@pytest.mark.parametrize(
    "skill", sorted(p.parent.name for p in SKILLS.glob("*/SKILL.md"))
)
def test_skill_frontmatter_follows_the_agent_skills_spec(skill):
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "SKILL.md starts with a frontmatter block"
    fields = dict(
        line.split(":", 1)
        for line in match.group(1).splitlines()
        if re.match(r"[a-z-]+:", line)
    )

    assert set(fields) <= FRONTMATTER_FIELDS
    assert fields["name"].strip() == skill
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", skill) and len(skill) <= 64
    assert 0 < len(fields["description"].strip()) <= 1024
    assert len(fields.get("compatibility", "").strip()) <= 500
