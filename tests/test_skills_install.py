"""judicata as plain Agent Skills (npx skills): prefixed skills with their own core.

An install copies only skill folders, so the review skill carries a copy of the
core in skills/review/core/, built from scripts/, templates/ and
agents/reviewer.md, and plain installs get the skills of skills/ under prefixed
names in .agents/skills/judicata-*/ (the plugin skills are marked internal).
tools/bundle_core.py builds both. These tests keep the copies equal to the
source and run the core from an installed copy.
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
# Prefixed copies for plain installs (npx skills), generated from skills/.
COPIES = REPO / ".agents" / "skills"
PLUGIN_SKILLS = sorted(p.parent.name for p in SKILLS.glob("*/SKILL.md"))
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


def test_the_bundled_core_and_the_prefixed_copies_match_the_source():
    result = bundle("--check")

    assert result.returncode == 0, result.stdout + result.stderr


def copy_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for part in ("scripts", "templates", "agents", "skills", ".agents"):
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
    """The prefixed skill folders alone, copied as npx skills does, without the plugin."""
    skills = tmp_path / "host" / ".agents" / "skills"
    shutil.copytree(COPIES, skills, ignore=IGNORE)
    project.script = skills / "judicata-review" / "core" / "scripts" / "judicata.py"
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
    assert (installed / "judicata-review" / "core" / "agents" / "reviewer.md").is_file()


def test_the_installed_core_names_the_prefixed_skills(project, installed):
    project.ok("contract", "create", stdin=proposal(project, "prose"))
    path = project.root / ".judicata" / "contracts" / "prose.toml"
    header = "".join(path.read_text(encoding="utf-8").splitlines()[:3])
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('text = "', 'text = "Edited: ', 1), encoding="utf-8")

    context = project.ok("session", "summary")["context"]
    edited = project.fail("round", "start")

    assert "/judicata-review" in context and "/judicata-contract" in context
    assert "/judicata:" not in context
    assert edited["code"] == "contract_edited"
    assert "/judicata-contract" in edited["message"]
    # the contract file is shared by every install, so it names both
    assert "/judicata:contract" in header and "judicata-contract" in header


def test_installed_skills_find_each_other_and_the_core(installed):
    for skill in sorted(installed.iterdir()):
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        paths = set(re.findall(r"<(?:skills|core)>/[\w./-]+", text))
        paths |= set(re.findall(r"CLAUDE_PLUGIN_ROOT\}/skills/[\w./-]+", text))

        assert f"<skills>/{skill.name}/SKILL.md" in paths
        assert "<skills>/judicata-review/core/scripts/judicata.py" in paths or (
            skill.name == "judicata-review" and "<core>/scripts/judicata.py" in paths
        )
        for path in paths:
            resolved = (
                path.replace("<core>", "<skills>/judicata-review/core")
                .replace("CLAUDE_PLUGIN_ROOT}/skills", "<skills>")
                .replace("<skills>", str(installed))
                .rstrip(".")
            )
            assert Path(resolved).exists(), f"{skill.name} names {path}"
        # nothing points to the plugin names any more
        assert not re.search(r"judicata:(review|contract|status)\b", text)
        assert not re.search(r"skills>?/(review|contract|status)/", text)
        assert not re.search(r"\bthe `?(review|contract|status)`?\s+skill", text)


@pytest.mark.parametrize(
    "folder",
    [SKILLS / name for name in PLUGIN_SKILLS]
    + [COPIES / f"judicata-{name}" for name in PLUGIN_SKILLS],
    ids=lambda folder: folder.relative_to(REPO).as_posix(),
)
def test_skill_frontmatter_follows_the_agent_skills_spec(folder):
    skill = folder.name
    text = (folder / "SKILL.md").read_text(encoding="utf-8")
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


def frontmatter(path: Path) -> str:
    match = re.match(r"---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.DOTALL)
    assert match, f"{path} starts with a frontmatter block"
    return match.group(1)


def test_plain_installs_get_prefixed_copies_of_the_plugin_skills():
    assert PLUGIN_SKILLS == ["contract", "review", "status"]
    copies = sorted(p.parent.name for p in COPIES.glob("*/SKILL.md"))

    assert copies == [f"judicata-{name}" for name in PLUGIN_SKILLS]
    for name in PLUGIN_SKILLS:
        plugin = frontmatter(SKILLS / name / "SKILL.md")
        copy = frontmatter(COPIES / f"judicata-{name}" / "SKILL.md")
        # npx skills hides the plugin skills (boolean true), shows only the copies
        assert re.search(r"^metadata:\n  internal: true$", plugin, re.MULTILINE)
        assert "internal" not in copy
        assert re.search(rf"^name: judicata-{name}$", copy, re.MULTILINE)
    assert (COPIES / "judicata-review" / "core" / "scripts" / "judicata.py").is_file()
    # a lock file naming the copies makes npx skills skip them as installed
    assert not (REPO / "skills-lock.json").exists()


def test_drifted_copies_are_reported_and_rebuilt(tmp_path):
    root = copy_repo(tmp_path)
    copies = root / ".agents" / "skills"
    (copies / "judicata-status" / "SKILL.md").write_text("by hand\n")
    (copies / "judicata-review" / "core" / "LICENSE").write_text("by hand\n")
    shutil.copytree(copies / "judicata-status", copies / "judicata-old")
    contract = root / "skills" / "contract" / "SKILL.md"
    contract.write_text(contract.read_text() + "\nSee the status skill.\n")

    checked = bundle("--check", root=root)

    assert checked.returncode == 1
    for path in (
        ".agents/skills/judicata-status/SKILL.md",
        ".agents/skills/judicata-review/core/LICENSE",
        ".agents/skills/judicata-old/SKILL.md",
        ".agents/skills/judicata-contract/SKILL.md",
    ):
        assert path in checked.stdout
    assert bundle(root=root).returncode == 0
    assert bundle("--check", root=root).returncode == 0
    assert not (copies / "judicata-old").exists()
    text = (copies / "judicata-contract" / "SKILL.md").read_text()
    assert text.endswith("\nSee the judicata-status skill.\n")


@pytest.mark.parametrize("broken", ["visible", "nested", "lost", "lock"])
def test_a_visible_or_lost_plugin_skill_or_a_lock_file_fails_and_changes_nothing(
    tmp_path, broken
):
    root = copy_repo(tmp_path)
    status = root / "skills" / "status" / "SKILL.md"
    expected = "skills/status/SKILL.md"
    if broken == "visible":
        status.write_text(status.read_text().replace("  internal: true\n", ""))
    elif broken == "nested":  # metadata.display.internal is not metadata.internal
        nested = "  display:\n    internal: true\n"
        status.write_text(status.read_text().replace("  internal: true\n", nested))
    elif broken == "lost":
        status.unlink()
    else:
        (root / "skills-lock.json").write_text("{}\n")
        expected = "skills-lock.json"
    copy = root / ".agents" / "skills" / "judicata-status" / "SKILL.md"
    before = copy.read_bytes()

    for result in (bundle("--check", root=root), bundle(root=root)):
        assert result.returncode == 2
        assert expected in result.stdout
    assert copy.read_bytes() == before


def test_the_copy_takes_its_name_from_the_folder(tmp_path):
    root = copy_repo(tmp_path)
    status = root / "skills" / "status" / "SKILL.md"
    status.write_text(status.read_text().replace("name: status", 'name: "status"'))

    assert bundle(root=root).returncode == 0
    copy = root / ".agents" / "skills" / "judicata-status" / "SKILL.md"
    assert re.search(r"^name: judicata-status$", frontmatter(copy), re.MULTILINE)
