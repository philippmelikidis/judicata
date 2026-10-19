"""Several contracts: every artifact file belongs to exactly one of them.

contract resolve maps artifact paths to contracts by their path patterns. A file
two contracts match is asked about once (contract assign stores the answer in
the ledger); a file no contract matches goes to the interview.
"""

import json
import subprocess

import pytest
from conftest import judgment_contract, proposal, reviewer_output


def files(project, *paths: str) -> None:
    for path in paths:
        target = project.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("text\n", encoding="utf-8")


def create(project, name: str, template: str, paths: list[str]) -> None:
    project.ok(
        "contract", "create", stdin=proposal(project, template, name=name, paths=paths)
    )


def resolve(project, *paths: str) -> dict:
    args = []
    for path in paths:
        args += ["--for", path]
    return project.ok("contract", "resolve", *args)


def assign_args(path: str, name: str, among: tuple[str, ...]) -> list[str]:
    args = ["contract", "assign", "--for", path, "--contract", name]
    for other in among:
        args += ["--among", other]
    return args


def resolved(result: dict) -> dict[str, list[str]]:
    return {group["contract"]: group["files"] for group in result["resolved"]}


@pytest.fixture
def code_and_thesis(project):
    files(project, "src/app.py", "src/lib/util.py", "thesis/ch1.md", "README.md")
    create(project, "code", "code", ["src/**"])
    create(project, "thesis", "prose", ["thesis/*.md"])
    return project


def test_each_file_goes_to_the_one_contract_whose_patterns_match(code_and_thesis):
    result = resolve(code_and_thesis, "src", "thesis/ch1.md")

    assert resolved(result) == {
        "code": ["src/app.py", "src/lib/util.py"],
        "thesis": ["thesis/ch1.md"],
    }
    assert result["ambiguous"] == []
    assert result["unmatched"] == []
    assert result["complete"] is True


def test_the_whole_project_splits_into_contracts_and_unmatched_files(code_and_thesis):
    result = resolve(code_and_thesis, ".")

    assert resolved(result) == {
        "code": ["src/app.py", "src/lib/util.py"],
        "thesis": ["thesis/ch1.md"],
    }
    assert result["unmatched"] == [
        {"path": ".", "files": ["README.md"], "file_count": 1}
    ]
    assert result["complete"] is False


def test_absolute_paths_inside_the_project_work(code_and_thesis):
    result = resolve(code_and_thesis, str(code_and_thesis.root / "src" / "app.py"))

    assert resolved(result) == {"code": ["src/app.py"]}


BOTH = ("thesis", "docs")


@pytest.fixture
def overlapping(project):
    files(project, "thesis/ch1.md", "thesis/ch2.md", "notes/todo.md")
    create(project, "docs", "docs", ["**/*.md"])
    create(project, "thesis", "prose", ["thesis/*.md"])
    return project


def test_two_matching_contracts_make_one_question_per_artifact(overlapping):
    result = resolve(overlapping, "thesis")

    assert result["resolved"] == []
    assert result["ambiguous"] == [
        {
            "path": "thesis",
            "contracts": ["thesis", "docs"],
            "files": ["thesis/ch1.md", "thesis/ch2.md"],
            "file_count": 2,
        }
    ]
    assert result["complete"] is False
    assert not any(
        e["type"] == "contract_assigned" for e in overlapping.ledger_events()
    )


def test_the_stored_answer_holds_in_every_later_call(overlapping):
    answer = overlapping.ok(*assign_args("thesis", "thesis", BOTH))
    assert answer["path"] == "thesis"
    assert answer["contract"] == "thesis"
    assert answer["file_count"] == 2
    assert overlapping.ledger_events()[-1]["type"] == "contract_assigned"

    # Each call is a fresh process, like a new session: only the ledger remembers.
    first = resolve(overlapping, "thesis")
    again = resolve(overlapping, "thesis/ch2.md")

    assert resolved(first) == {"thesis": ["thesis/ch1.md", "thesis/ch2.md"]}
    assert first["resolved"][0]["assigned"] == ["thesis"]
    assert first["ambiguous"] == []
    assert first["complete"] is True
    assert resolved(again) == {"thesis": ["thesis/ch2.md"]}


def test_an_answer_covers_only_its_path_and_a_closer_or_later_one_wins(overlapping):
    def assign(path, name):
        overlapping.ok(*assign_args(path, name, BOTH))

    assign("thesis/ch1.md", "thesis")
    result = resolve(overlapping, ".")
    assert resolved(result) == {
        "docs": ["notes/todo.md"],
        "thesis": ["thesis/ch1.md"],
    }
    assert [(a["path"], a["files"]) for a in result["ambiguous"]] == [
        (".", ["thesis/ch2.md"])
    ]

    assign("thesis", "docs")
    assert resolved(resolve(overlapping, "thesis")) == {
        "docs": ["thesis/ch2.md"],
        "thesis": ["thesis/ch1.md"],
    }

    assign("thesis/ch1.md", "docs")
    assert resolved(resolve(overlapping, "thesis")) == {
        "docs": ["thesis/ch1.md", "thesis/ch2.md"]
    }


def test_an_answer_never_overrides_the_path_patterns(overlapping):
    overlapping.ok(*assign_args(".", "thesis", BOTH))

    result = resolve(overlapping, ".")

    assert resolved(result) == {
        "docs": ["notes/todo.md"],
        "thesis": ["thesis/ch1.md", "thesis/ch2.md"],
    }


def test_assign_refuses_a_contract_that_does_not_match_the_path(overlapping):
    error = overlapping.fail(*assign_args("notes/todo.md", "thesis", BOTH))
    assert error["code"] == "contract_not_matching"
    assert error["details"]["contracts"] == ["docs"]

    missing = overlapping.fail(*assign_args("thesis", "prose", ("prose", "docs")))
    assert missing["code"] == "contract_not_found"
    for among in [("docs",), ("docs", "docs"), ("docs", "nope")]:
        error = overlapping.fail(*assign_args("thesis", "docs", among))
        assert error["code"] in ("invalid_input", "contract_not_found"), among
    error = overlapping.fail(*assign_args("thesis", "thesis", ("docs", "nope")))
    assert error["code"] == "invalid_input"
    assert not any(
        e["type"] == "contract_assigned" for e in overlapping.ledger_events()
    )


def test_no_matching_contract_leaves_the_files_for_the_interview(code_and_thesis):
    result = resolve(code_and_thesis, "README.md")

    assert result["resolved"] == []
    assert result["ambiguous"] == []
    assert result["unmatched"] == [
        {"path": "README.md", "files": ["README.md"], "file_count": 1}
    ]
    assert result["complete"] is False


def test_without_any_contract_everything_is_unmatched(project):
    files(project, "letter.md")

    result = resolve(project, "letter.md")

    assert result["unmatched"][0]["files"] == ["letter.md"]
    assert result["contracts"] == []


def test_the_contract_from_the_interview_takes_the_unmatched_files(code_and_thesis):
    create(code_and_thesis, "readme", "docs", ["README.md"])

    result = resolve(code_and_thesis, "README.md")

    assert resolved(result) == {"readme": ["README.md"]}
    assert result["complete"] is True


def test_paths_must_be_inside_the_project(code_and_thesis, tmp_path):
    outside = tmp_path / "elsewhere.md"
    outside.write_text("x\n", encoding="utf-8")
    error = code_and_thesis.fail("contract", "resolve", "--for", str(outside))
    assert error["code"] == "path_outside_project"
    error = code_and_thesis.fail(
        *assign_args("../elsewhere.md", "code", ("code", "thesis"))
    )
    assert error["code"] == "path_outside_project"


def test_a_path_without_files_in_scope_is_reported(code_and_thesis):
    (code_and_thesis.root / "empty").mkdir()

    result = resolve(code_and_thesis, "empty", "src/app.py")

    assert result["no_files"] == ["empty"]
    assert resolved(result) == {"code": ["src/app.py"]}


def test_a_deleted_file_of_a_diff_does_not_stop_the_others(code_and_thesis):
    result = resolve(code_and_thesis, "src/app.py", "src/gone.py")

    assert result["not_found"] == ["src/gone.py"]
    assert resolved(result) == {"code": ["src/app.py"]}
    assert result["complete"] is True

    error = code_and_thesis.fail(
        *assign_args("src/gone.py", "code", ("code", "thesis"))
    )
    assert error["code"] == "path_not_found"


def test_gitattributes_is_unmatched_only_when_named(code_and_thesis):
    (code_and_thesis.root / ".gitattributes").write_text("* text=auto\n")

    assert resolve(code_and_thesis, ".")["unmatched"][0]["files"] == ["README.md"]
    named = resolve(code_and_thesis, ".gitattributes")
    assert named["unmatched"][0]["files"] == [".gitattributes"]


def test_ignored_files_are_not_assigned_in_git(code_and_thesis):
    root = code_and_thesis.root
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    files(code_and_thesis, "src/build/out.py")
    (root / ".gitignore").write_text("src/build/\n", encoding="utf-8")

    result = resolve(code_and_thesis, "src")

    assert resolved(result) == {"code": ["src/app.py", "src/lib/util.py"]}


def test_long_file_lists_are_cut_but_counted(project):
    files(project, *(f"src/m{i:03}.py" for i in range(60)))
    create(project, "code", "code", ["src/**"])

    group = resolve(project, "src")["resolved"][0]

    assert len(group["files"]) == 50
    assert group["file_count"] == 60


def test_an_invalid_contract_stops_the_resolution(code_and_thesis):
    code_and_thesis.write_contract("broken", 'name = "broken"\n')

    error = code_and_thesis.fail("contract", "resolve", "--for", "src")

    assert error["code"] == "invalid_contract"


def test_answers_to_different_questions_on_one_path_stay_apart(project):
    files(project, "x.md", "y.md")
    create(project, "a", "docs", ["*.md"])
    create(project, "b", "prose", ["x.md"])
    create(project, "c", "prose", ["y.md"])

    questions = resolve(project, ".")["ambiguous"]
    assert [(q["path"], q["contracts"], q["files"]) for q in questions] == [
        (".", ["b", "a"], ["x.md"]),
        (".", ["c", "a"], ["y.md"]),
    ]

    project.ok(*assign_args(".", "b", ("b", "a")))
    project.ok(*assign_args(".", "a", ("c", "a")))

    assert resolved(resolve(project, ".")) == {"a": ["y.md"], "b": ["x.md"]}


def test_a_new_matching_contract_asks_again(overlapping):
    overlapping.ok(*assign_args("thesis", "thesis", BOTH))
    create(overlapping, "chapters", "prose", ["thesis/**"])

    questions = resolve(overlapping, "thesis")["ambiguous"]

    assert [sorted(q["contracts"]) for q in questions] == [
        ["chapters", "docs", "thesis"]
    ]


DASH_RULE = """
[[criteria]]
id = "R-1"
text = "No X"
check = "pattern"
required = true
forbid = "X"
"""


def test_a_round_skips_files_answered_for_another_contract(project):
    files(project, "notes/todo.md")
    (project.root / "thesis").mkdir()
    (project.root / "thesis" / "ch1.md").write_text("X marks it\n", encoding="utf-8")
    for name, paths in [("docs", '["**/*.md"]'), ("thesis", '["thesis/*.md"]')]:
        project.write_contract(
            name,
            f'name = "{name}"\ntemplate = "prose"\npaths = {paths}\n' + DASH_RULE,
        )

    assert project.review("--contract", "docs")["outcome"] == "continue"

    project.ok(*assign_args("thesis/ch1.md", "thesis", BOTH))

    assert project.review("--contract", "docs")["outcome"] == "done"
    assert project.review("--contract", "thesis")["outcome"] == "continue"


def test_reviewers_and_the_revision_leave_out_files_of_another_contract(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())
    app = judgment_contract().replace('name = "code"', 'name = "app"')
    project.write_contract(
        "app", app.replace('paths = ["**"]', 'paths = ["src/app.py"]')
    )

    before = project.ok("round", "start", "--contract", "code")["revision"]["files"]
    project.ok(*assign_args("src/app.py", "app", ("app", "code")))

    started = project.ok("round", "start", "--contract", "code")
    assert started["revision"]["files"] == before - 1
    round_id = started["round"]
    project.ok("round", "check", "--round", round_id)
    plan = project.ok("review", "plan", "--round", round_id)
    assert plan["exclude"] == ["src/app.py"]

    code, answer = project.submit(round_id, plan["runs"][0], reviewer_output("fail"))
    assert code != 0, answer
    assert "inside the contract's scope" in json.dumps(answer["error"])
