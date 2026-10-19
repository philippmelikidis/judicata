"""The first write makes Git merge the ledger by union, without harming the file."""

from conftest import command_contract

LINE = ".judicata/ledger.jsonl merge=union"


def gitattributes(project) -> str:
    return (project.root / ".gitattributes").read_text(encoding="utf-8")


def test_gitattributes_is_created_on_the_first_round(project):
    project.write_contract("code", command_contract("true"))
    assert not (project.root / ".gitattributes").exists()

    project.review()

    assert gitattributes(project).splitlines() == [LINE]


def test_existing_gitattributes_keeps_its_lines_and_gets_the_line_once(project):
    existing = "* text=auto\n*.png binary\n"
    (project.root / ".gitattributes").write_text(existing, encoding="utf-8")
    project.write_contract("code", command_contract("true"))

    project.review()
    project.review()

    assert gitattributes(project) == existing + LINE + "\n"


def test_missing_final_newline_is_not_glued_to_the_new_line(project):
    (project.root / ".gitattributes").write_text("* text=auto", encoding="utf-8")
    project.write_contract("code", command_contract("true"))

    project.review()

    assert gitattributes(project).splitlines() == ["* text=auto", LINE]


def test_an_equivalent_existing_line_is_respected(project):
    existing = "/.judicata/ledger.jsonl   merge=union\n"
    (project.root / ".gitattributes").write_text(existing, encoding="utf-8")
    project.write_contract("code", command_contract("true"))

    project.review()

    assert gitattributes(project) == existing


def test_git_really_merges_the_ledger_by_union(project):
    import subprocess

    def git(*args):
        subprocess.run(
            ["git", *args], cwd=project.root, check=True, capture_output=True
        )

    git("init", "-q")
    project.write_contract("code", command_contract("true"))
    project.review()
    out = subprocess.run(
        ["git", "check-attr", "merge", ".judicata/ledger.jsonl"],
        cwd=project.root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.strip().endswith("merge: union")


def test_a_later_line_overriding_the_merge_driver_gets_the_line_again(project):
    existing = ".judicata/ledger.jsonl merge=union\n* merge=text\n"
    (project.root / ".gitattributes").write_text(existing, encoding="utf-8")
    project.write_contract("code", command_contract("true"))

    project.review()
    project.review()

    assert gitattributes(project) == existing + LINE + "\n"
