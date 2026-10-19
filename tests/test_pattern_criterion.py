"""Pattern criteria: the core checks the files in scope itself, no model involved."""

import json
import os
import subprocess
import time

import pytest
from conftest import proposal

EM, EN = "\u2014", "\u2013"  # the dashes a prose contract forbids


def pattern_contract(*rules: tuple[str, object], paths=("letter.md",)) -> str:
    """A prose contract with one required pattern criterion per (rule, value)."""
    lines = [
        'name = "letter"',
        'template = "prose"',
        f"paths = {json.dumps(list(paths))}",
    ]
    for number, (rule, value) in enumerate(rules, start=1):
        lines += [
            "",
            "[[criteria]]",
            f'id = "PROSE-{number}"',
            f'text = "Rule {rule}"',
            'check = "pattern"',
            "required = true",
            f"{rule} = {json.dumps(value)}",
        ]
    return "\n".join(lines) + "\n"


def write(project, name: str, text: str) -> None:
    path = project.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_forbidden_expression_found_is_a_blocker_with_file_and_line(project):
    project.write_contract("letter", pattern_contract(("forbid", f"[{EM}{EN}]")))
    write(project, "letter.md", f"Dear team,\n\nI build tools {EM} mostly.\nThanks\n")

    result = project.review()

    assert result["outcome"] == "continue"
    [blocker] = result["blockers"]
    assert blocker["criterion"] == "PROSE-1"
    assert blocker["severity"] == "blocker"
    [hit] = blocker["evidence"]["hits"]
    assert hit["file"] == "letter.md"
    assert hit["line"] == 3
    assert hit["text"] == f"I build tools {EM} mostly."


def test_required_expression_must_occur_in_every_file_in_scope(project):
    contract = pattern_contract(("require", "^Kind regards"), paths=["letters/*.md"])
    project.write_contract("letter", contract)
    write(project, "letters/a.md", "Hello,\n\ntext\n\nKind regards\nSam\n")
    write(project, "letters/b.md", "Hello,\n\ntext\n\nkind regards\n")

    result = project.review()

    [blocker] = result["blockers"]
    evidence = blocker["evidence"]
    assert evidence["missing"] == ["letters/b.md"]
    assert [(h["file"], h["line"]) for h in evidence["hits"]] == [("letters/a.md", 5)]

    write(project, "letters/b.md", "Hello,\n\nKind regards\n")
    assert project.review()["outcome"] == "done"


def test_application_contract_with_no_dashes_and_at_most_400_words(project):
    contract = pattern_contract(("forbid", f"[{EM}{EN}]"), ("max_words", 400))
    project.write_contract("letter", contract)
    # 4 lines of 100 words, then one more word on line 6 (line 5 is empty)
    body = "\n".join(" ".join(["word"] * 100) for _ in range(4))
    write(project, "letter.md", body + "\n\n# - one\n")

    result = project.review()

    assert result["outcome"] == "continue"
    [blocker] = result["blockers"]
    assert blocker["criterion"] == "PROSE-2"
    [hit] = blocker["evidence"]["hits"]
    assert (hit["file"], hit["line"], hit["count"]) == ("letter.md", 6, 401)
    assert blocker["evidence"]["limit"] == 400
    assert result["passed"] == ["PROSE-1"]

    write(project, "letter.md", body + "\n\n# - \n")
    assert project.review()["outcome"] == "done"


def test_at_most_n_lines_counts_every_line_per_file(project):
    contract = pattern_contract(("max_lines", 3), paths=["docs/**"])
    project.write_contract("letter", contract)
    write(project, "docs/short.md", "one\n\nthree\n")
    write(project, "docs/deep/long.md", "one\ntwo\n\nfour\nfive")

    result = project.review()

    [blocker] = result["blockers"]
    [hit] = blocker["evidence"]["hits"]
    assert (hit["file"], hit["line"], hit["count"]) == ("docs/deep/long.md", 4, 5)
    assert hit["text"] == "four"
    assert blocker["evidence"]["files_checked"] == 2


def test_failing_optional_pattern_is_should_fix_and_does_not_block(project):
    contract = pattern_contract(("max_lines", 1)).replace(
        "required = true", "required = false"
    )
    project.write_contract("letter", contract)
    write(project, "letter.md", "one\ntwo\n")

    result = project.review()

    assert result["outcome"] == "done"
    assert result["blockers"] == []
    [should_fix] = result["should_fix"]
    assert (should_fix["criterion"], should_fix["severity"]) == (
        "PROSE-1",
        "should-fix",
    )
    assert should_fix["evidence"]["hits"][0]["line"] == 2


@pytest.mark.parametrize("rule", ["forbid", "require"])
def test_invalid_regular_expression_is_rejected_before_anything_runs(project, rule):
    project.write_contract("letter", pattern_contract((rule, "(unclosed")))
    write(project, "letter.md", "text\n")

    error = project.fail("round", "start")

    assert error["code"] == "invalid_contract"
    [problem] = error["details"]["problems"]
    assert problem["field"] == f"criteria[0] (PROSE-1).{rule}"
    assert problem["problem"].startswith("invalid regular expression")
    assert not project.ledger.exists()


def test_results_are_the_same_in_every_run(project):
    contract = pattern_contract(
        ("forbid", "very"),
        ("require", "^Kind regards"),
        ("max_words", 5),
        ("max_lines", 2),
        paths=["**"],
    )
    project.write_contract("letter", contract)
    write(project, "b.md", "very very good\nand very nice\nreally\n")
    write(project, "a.md", "Kind regards\n")
    write(project, "sub/c.txt", "nothing very special here at all today\n")

    runs = [project.review() for _ in range(3)]

    for result in runs:
        result.pop("round")
        for key in ("outcome", "outcome_label", "stop"):  # the stop rule counts on
            result.pop(key)
    assert runs[0] == runs[1] == runs[2]
    forbid = runs[0]["blockers"][0]["evidence"]
    assert [(h["file"], h["line"], h["column"]) for h in forbid["hits"]] == [
        ("b.md", 1, 1),
        ("b.md", 1, 6),
        ("b.md", 2, 5),
        ("sub/c.txt", 1, 9),
    ]
    assert [b["criterion"] for b in runs[0]["blockers"]] == [
        "PROSE-1",
        "PROSE-2",
        "PROSE-3",
        "PROSE-4",
    ]


def test_pattern_over_an_empty_scope_fails_instead_of_passing_silently(project):
    project.write_contract("letter", pattern_contract(("forbid", "x")))
    write(project, "letters/letter.md", "no x at the root\n")

    [blocker] = project.review()["blockers"]

    assert blocker["evidence"]["files_checked"] == 0
    assert "no files in scope" in blocker["evidence"]["problem"]


def test_ledger_contracts_and_git_ignored_files_are_not_in_scope(project):
    subprocess.run(["git", "init", "-q", str(project.root)], check=True)
    # the contract itself contains the forbidden word, so does every ledger entry
    project.write_contract(
        "letter", pattern_contract(("forbid", "secret"), paths=["**"])
    )
    write(project, ".gitignore", "build/\n")
    write(project, "build/out.txt", "secret\n")
    write(project, "draft.md", "nothing to hide\n")

    first = project.review()
    assert first["outcome"] == "done", first

    write(project, "notes/todo.md", "one\nthe secret plan\n")
    [blocker] = project.review()["blockers"]
    [hit] = blocker["evidence"]["hits"]
    assert (hit["file"], hit["line"]) == ("notes/todo.md", 2)
    # .gitignore, .gitattributes (written by the core), draft.md, notes/todo.md
    assert blocker["evidence"]["files_checked"] == 4


def test_without_git_every_file_below_the_root_is_a_candidate(project):
    project.write_contract(
        "letter", pattern_contract(("forbid", "secret"), paths=["**"])
    )
    write(project, "build/out.txt", "secret\n")

    [blocker] = project.review()["blockers"]

    assert [h["file"] for h in blocker["evidence"]["hits"]] == ["build/out.txt"]


def test_binary_files_are_skipped_and_listed(project):
    project.write_contract(
        "letter", pattern_contract(("forbid", "secret"), paths=["**"])
    )
    (project.root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nsecret\x00\xff")
    write(project, "letter.md", "fine\n")

    round_id = project.ok("round", "start")["round"]
    [checked] = project.ok("round", "check", "--round", round_id)["results"]

    assert checked["passed"] is True, checked
    assert checked["evidence"]["skipped"] == [{"file": "logo.png", "reason": "binary"}]
    assert checked["evidence"]["files_checked"] == 2  # letter.md, .gitattributes


def test_text_that_cannot_be_read_fails_instead_of_passing_unchecked(project):
    project.write_contract(
        "letter", pattern_contract(("forbid", "secret"), paths=["*.txt"])
    )
    (project.root / "latin1.txt").write_bytes("caf\xe9 secret\n".encode("latin-1"))
    write(project, "fine.txt", "fine\n")

    [blocker] = project.review()["blockers"]

    evidence = blocker["evidence"]
    assert evidence["hits"] == []
    assert evidence["unreadable"] == [{"file": "latin1.txt", "reason": "not UTF-8"}]
    assert "could not be read" in evidence["problem"]


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root reads anything"
)
def test_file_without_read_permission_fails_the_criterion(project):
    project.write_contract(
        "letter", pattern_contract(("require", "SIGNATURE"), paths=["*.md"])
    )
    write(project, "good.md", "SIGNATURE\n")
    write(project, "locked.md", "no signature\n")
    (project.root / "locked.md").chmod(0)
    try:
        [blocker] = project.review()["blockers"]
    finally:
        (project.root / "locked.md").chmod(0o644)

    [entry] = blocker["evidence"]["unreadable"]
    assert entry["file"] == "locked.md"


def test_expression_that_runs_too_long_is_stopped_and_fails(project):
    contract = pattern_contract(("require", "(a+)+$")) + "timeout = 1\n"
    project.write_contract("letter", contract)
    write(project, "letter.md", "a" * 40 + "!\n")

    started = time.monotonic()
    [blocker] = project.review()["blockers"]

    assert time.monotonic() - started < 20
    assert blocker["evidence"]["timed_out"] is True
    assert "timed out after 1 s" in blocker["evidence"]["problem"]


def test_pattern_timeout_is_validated(project):
    contract = pattern_contract(("forbid", "x")) + "timeout = 0\n"
    project.write_contract("letter", contract)

    error = project.fail("round", "start")

    assert any(p["field"].endswith(".timeout") for p in error["details"]["problems"])


def test_a_million_hits_stay_fast(project):
    project.write_contract("letter", pattern_contract(("forbid", "x")))
    write(project, "letter.md", "x\n" * 1_000_000)

    started = time.monotonic()
    [blocker] = project.review()["blockers"]

    assert time.monotonic() - started < 30
    assert blocker["evidence"]["hit_count"] == 1_000_000
    assert blocker["evidence"]["hits"][-1]["line"] == len(blocker["evidence"]["hits"])


def test_invalid_path_pattern_is_rejected_by_validation(project):
    project.write_contract(
        "letter", pattern_contract(("forbid", "x"), paths=["ok.md", "[z-a].md"])
    )

    error = project.fail("round", "start")

    assert error["code"] == "invalid_contract"
    [problem] = error["details"]["problems"]
    assert problem["field"] == "paths[1]"
    assert "invalid path pattern" in problem["problem"]


@pytest.mark.parametrize("root", ["./", ".", "/"])
def test_project_root_as_path_covers_every_file(project, root):
    project.write_contract("letter", pattern_contract(("forbid", "x"), paths=[root]))
    write(project, "sub/letter.md", "x\n")

    [blocker] = project.review()["blockers"]

    assert [h["file"] for h in blocker["evidence"]["hits"]] == ["sub/letter.md"]


def test_path_patterns_select_the_files_in_scope(project):
    paths = ["src", "*.md", "docs/**/*.txt", "data/file?.[ch]sv"]
    project.write_contract("letter", pattern_contract(("forbid", "x"), paths=paths))
    files = [
        "src/a.py",
        "src/deep/b.py",
        "srcfoo/c.py",
        "top.md",
        "sub/top.md",
        "docs/x.txt",
        "docs/a/b/y.txt",
        "docs/z.md",
        "data/file1.csv",
        "data/file2.hsv",
        "data/file10.csv",
        "data/file3.tsv",
    ]
    for name in files:
        write(project, name, "x\n")

    [blocker] = project.review()["blockers"]

    assert [h["file"] for h in blocker["evidence"]["hits"]] == [
        "data/file1.csv",
        "data/file2.hsv",
        "docs/a/b/y.txt",
        "docs/x.txt",
        "src/a.py",
        "src/deep/b.py",
        "top.md",
    ]


def test_many_hits_are_counted_but_only_the_first_are_kept(project):
    project.write_contract("letter", pattern_contract(("forbid", "x")))
    write(project, "letter.md", "x\n" * 500)

    [blocker] = project.review()["blockers"]

    evidence = blocker["evidence"]
    assert evidence["hit_count"] == 500
    assert 0 < len(evidence["hits"]) < 500
    assert evidence["hits"][0]["line"] == 1


def test_windows_line_endings_give_the_same_lines_and_anchors(project):
    project.write_contract("letter", pattern_contract(("forbid", " $")))
    (project.root / "letter.md").write_bytes(b"\xef\xbb\xbfclean\r\ntrailing \r\nend")

    [blocker] = project.review()["blockers"]

    [hit] = blocker["evidence"]["hits"]
    assert (hit["line"], hit["column"], hit["text"]) == (2, 9, "trailing ")


def test_pattern_timeout_beyond_float_range_is_a_contract_problem(project):
    draft = proposal(project, "prose")
    pattern = next(c for c in draft["criteria"] if c["check"] == "pattern")
    pattern["timeout"] = 10**400

    error = project.fail("contract", "validate", stdin=json.dumps(draft))

    assert error["code"] == "invalid_contract", error
    assert any(p["field"].endswith(".timeout") for p in error["details"]["problems"])
