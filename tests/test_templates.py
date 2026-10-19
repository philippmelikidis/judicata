"""Templates (code, docs, prose) are proposals for the interview, not contracts."""

import pytest


def test_three_templates_are_listed_with_a_description(project):
    listed = project.ok("template", "list")["templates"]

    assert [t["name"] for t in listed] == ["code", "docs", "prose"]
    for template in listed:
        assert template["description"]
        assert template["criteria"] >= 3


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (["src/app.py", "src/util.py"], "code"),
        (["README.md"], "docs"),
        (["docs/guide/install.md"], "docs"),
        (["letters/cover-letter.md"], "prose"),
        (["thesis/chapter-2.tex", "src/plot.py", "thesis/chapter-3.tex"], "prose"),
    ],
)
def test_a_template_is_suggested_by_artifact_type(project, files, expected):
    args = [arg for file in files for arg in ("--for", file)]

    assert project.ok("template", "list", *args)["suggested"] == expected


def test_no_suggestion_without_artifact_or_for_unknown_types(project):
    assert project.ok("template", "list")["suggested"] is None
    assert project.ok("template", "list", "--for", "photo.jpg")["suggested"] is None


def test_a_directory_is_suggested_by_the_files_in_it(project):
    (project.root / "src").mkdir()
    for name in ("a.py", "b.py", "notes.md"):
        (project.root / "src" / name).write_text("x\n", encoding="utf-8")

    listed = project.ok("template", "list", "--for", "src")

    assert listed["suggested"] == "code"
    matches = {t["name"]: t["matches"] for t in listed["templates"]}
    assert matches == {"code": 2, "docs": 0, "prose": 1}


def test_show_returns_the_template_and_a_proposal_to_edit(project):
    shown = project.ok("template", "show", "--template", "prose")

    criteria = {c["id"]: c for c in shown["template"]["criteria"]}
    assert set(criteria) == {"PROSE-1", "PROSE-2", "PROSE-3", "PROSE-4"}
    assert criteria["PROSE-2"]["check"] == "pattern"
    assert "max_words" in criteria["PROSE-2"]["fill"]
    assert criteria["PROSE-2"]["hint"]
    draft = shown["proposal"]
    assert draft["name"] == "prose" and draft["paths"] == []
    assert draft["reviewers"] == {"count": 2, "models": ["inherit"]}
    assert draft["stop"] == {"max_rounds": 5, "stall_rounds": 2}
    assert {"fill", "hint", "params"}.isdisjoint(draft["criteria"][1])
    assert draft["criteria"][1]["max_words"] == 400


def test_code_template_marks_only_the_acceptance_criteria_optional(project):
    criteria = project.ok("template", "show", "--template", "code")["template"][
        "criteria"
    ]

    assert {c["id"]: c["required"] for c in criteria} == {
        "CODE-1": True,
        "CODE-2": True,
        "CODE-3": True,
        "CODE-4": False,
    }
    assert [c["check"] for c in criteria] == [
        "command",
        "command",
        "judgment",
        "judgment",
    ]


def test_an_unknown_template_is_reported(project):
    error = project.fail("template", "show", "--template", "poetry")

    assert error["code"] == "template_not_found"
    assert error["details"]["templates"] == ["code", "docs", "prose"]


def test_dependencies_and_caches_do_not_decide_the_template(project):
    (project.root / "letter.md").write_text("Dear team,\n", encoding="utf-8")
    for folder in ("node_modules/pkg", "__pycache__", ".venv/lib"):
        (project.root / folder).mkdir(parents=True)
        for index in range(3):
            (project.root / folder / f"m{index}.js").write_text("x\n", encoding="utf-8")

    assert project.ok("template", "list", "--for", ".")["suggested"] == "prose"
