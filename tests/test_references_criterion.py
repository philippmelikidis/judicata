"""References criteria: links, anchors, images and citation keys must exist.

The core checks them itself, no model involved, through the same round as
every deterministic check (round start, check, close).
"""

import json


def references_contract(
    *, code_paths: bool | None = None, paths=("docs/**",), required: bool = True
) -> str:
    lines = [
        'name = "docs"',
        'template = "docs"',
        f"paths = {json.dumps(list(paths))}",
        "",
        "[[criteria]]",
        'id = "DOCS-1"',
        'text = "Every reference points to something that exists"',
        'check = "references"',
        f"required = {'true' if required else 'false'}",
    ]
    if code_paths is not None:
        lines.append(f"code_paths = {'true' if code_paths else 'false'}")
    return "\n".join(lines) + "\n"


def write(project, name: str, text: str) -> None:
    path = project.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def broken(result: dict) -> list[tuple]:
    """(file, line, reference, kind) of every broken reference of DOCS-1."""
    found = [
        item
        for group in ("blockers", "should_fix")
        for item in result[group]
        if item["criterion"] == "DOCS-1"
    ]
    if not found:
        return []
    [item] = found
    return [
        (b["file"], b["line"], b["reference"], b["kind"])
        for b in item["evidence"]["broken"]
    ]


def test_a_link_to_a_missing_file_is_a_blocker_with_file_and_line(project):
    project.write_contract("docs", references_contract())
    write(project, "docs/setup.md", "# Setup\n")
    write(
        project,
        "docs/guide.md",
        "# Guide\n\nSee [setup](setup.md) first.\n\nThen [deploy](deploy.md).\n",
    )

    result = project.review()

    assert result["outcome"] == "continue"
    [blocker] = result["blockers"]
    assert blocker["check"] == "references"
    [item] = blocker["evidence"]["broken"]
    assert (item["file"], item["line"], item["reference"]) == (
        "docs/guide.md",
        5,
        "deploy.md",
    )
    assert item["kind"] == "link"
    assert item["target"] == "docs/deploy.md"
    assert "does not exist" in item["reason"]

    write(project, "docs/deploy.md", "# Deploy\n")
    assert project.review()["outcome"] == "done"


def test_anchors_are_github_heading_slugs_in_the_target_file(project):
    project.write_contract("docs", references_contract())
    write(
        project,
        "docs/setup.md",
        "# Setup\n\n## Install `judicata` (v0.2)!\n\nText\n\n## FAQ\n\n## FAQ\n\n"
        'Options\n-------\n\n<a id="custom-place"></a>\n\n```\n# Not a heading\n```\n',
    )
    write(
        project,
        "docs/guide.md",
        "# Guide\n\n## Next steps\n\n"
        "[a](setup.md#install-judicata-v02) [b](setup.md#faq-1) [c](#next-steps)\n"
        "[d](setup.md#options) [e](setup.md#custom-place) [f](./setup.md#Setup)\n"
        "[g](setup.md#faq-2)\n"
        "[h](#not-a-heading) [i](setup.md#not-a-heading)\n",
    )

    result = project.review()

    assert broken(result) == [
        ("docs/guide.md", 7, "setup.md#faq-2", "link"),
        ("docs/guide.md", 8, "#not-a-heading", "link"),
        ("docs/guide.md", 8, "setup.md#not-a-heading", "link"),
    ]
    reasons = [b["reason"] for b in result["blockers"][0]["evidence"]["broken"]]
    assert "no heading or anchor #faq-2 in docs/setup.md" in reasons
    assert "no heading or anchor #not-a-heading in docs/guide.md" in reasons


def test_images_are_checked_and_external_urls_and_code_are_ignored(project):
    project.write_contract("docs", references_contract())
    write(project, "docs/img/logo.png", "png")
    write(
        project,
        "docs/guide.md",
        "# Guide\n\n"
        '![logo](img/logo.png) ![shot](img/shot.png "Screenshot")\n'
        '<img src="img/missing.svg" width="40">\n\n'
        "[site](https://example.com/nowhere.md) [mail](mailto:a@example.com)\n"
        "[cdn](//cdn.example.com/x.js) [ftp](ftp://host/file.txt)\n"
        "<https://example.com/auto.md>\n\n"
        "```markdown\n[in a fence](fenced.md)\n```\n\n"
        "    [indented code](indented.md)\n\n"
        "Inline `[code](code.md)` and <!-- [hidden](hidden.md) -->\n\n"
        "- a list\n\n    [list continuation](continued.md)\n\n"
        "[ref]: ./img/gone.png\n"
        "[^note]: A footnote, not a link.\n",
    )

    result = project.review()

    assert broken(result) == [
        ("docs/guide.md", 3, "img/shot.png", "image"),
        ("docs/guide.md", 4, "img/missing.svg", "image"),
        ("docs/guide.md", 20, "continued.md", "link"),
        ("docs/guide.md", 22, "./img/gone.png", "link"),
    ]


BIB = """% the library of the thesis
@string{acm = "ACM"}
@comment{@article{commented, title = {Not an entry}}}
@Article{knuth1984,
  author = {Donald E. Knuth},
  title = {Literate Programming},
}
@book{ lamport:latex ,
  title = {LaTeX},
}
@misc(web-2020, title = "A web page")
"""


def test_citation_keys_must_be_in_a_bib_file_of_the_project(project):
    contract = references_contract(paths=["thesis/**"])
    project.write_contract("docs", contract)
    write(project, "library/refs.bib", BIB)  # outside the scope, still used
    write(
        project,
        "thesis/intro.md",
        "# Intro\n\n"
        "As shown [@knuth1984; see -@lamport:latex, p. 3], and [@web-2020].\n"
        "Unknown [@nobody] and [see @ghost, ch. 2].\n"
        "Not citations: [mail me](mailto:me@example.com), [me@example.com],\n"
        "[@someone](https://example.com/someone), `[@code]`, [@commented].\n",
    )
    write(
        project,
        "thesis/chapter.tex",
        "\\section{Start}\n"
        "Known \\cite{knuth1984} and \\citep[p.~3]{lamport:latex, web-2020}.\n"
        "Unknown \\textcite{missing} and \\cite{knuth1984,absent}.\n"
        "100\\% sure \\cite{after-percent} % \\cite{commented-out}\n"
        "\\nocite{*}\n"
        "\\begin{verbatim}\n\\cite{in-verbatim}\n\\end{verbatim}\n",
    )

    result = project.review()

    assert broken(result) == [
        ("thesis/chapter.tex", 3, "missing", "citation"),
        ("thesis/chapter.tex", 3, "absent", "citation"),
        ("thesis/chapter.tex", 4, "after-percent", "citation"),
        ("thesis/intro.md", 4, "nobody", "citation"),
        ("thesis/intro.md", 4, "ghost", "citation"),
        ("thesis/intro.md", 6, "commented", "citation"),
    ]
    [first, *_] = result["blockers"][0]["evidence"]["broken"]
    assert first["reason"] == "missing is not a key in any .bib file of the project"
    assert first["target"] == "missing"


def test_citations_without_any_bib_file_are_broken(project):
    project.write_contract("docs", references_contract())
    write(project, "docs/paper.md", "# Paper\n\nAs [@knuth1984] says.\n")

    result = project.review()

    [item] = result["blockers"][0]["evidence"]["broken"]
    assert (item["line"], item["reference"]) == (3, "knuth1984")
    assert item["reason"] == "knuth1984 cannot be found, the project has no .bib file"


CODE_PATHS_DOC = (
    "# Build\n\n"
    "Run `scripts/build.sh`, then `./scripts/missing.sh` and `/docs/guide.md`.\n"
    "Not paths: `scripts`, `build.sh`, `make all/fast now.sh`, `https://x.org/a.js`\n"
    "and `src/` and `v1.2/3`.\n\n"
    "```sh\nsh scripts/gone.sh `tools/gone.py`\n```\n"
)


def test_code_paths_are_checked_relative_to_the_project_root_when_on(project):
    project.write_contract("docs", references_contract(code_paths=True))
    write(project, "scripts/build.sh", "echo\n")
    write(project, "docs/guide.md", CODE_PATHS_DOC)

    result = project.review()

    assert broken(result) == [
        ("docs/guide.md", 3, "./scripts/missing.sh", "code_path"),
    ]
    [missing] = result["blockers"][0]["evidence"]["broken"]
    assert missing["target"] == "scripts/missing.sh"
    assert result["blockers"][0]["evidence"]["code_paths"] is True


def test_code_paths_are_not_checked_by_default(project):
    project.write_contract("docs", references_contract())
    write(project, "docs/guide.md", CODE_PATHS_DOC)

    result = project.review()

    assert result["outcome"] == "done", result


def test_a_broken_reference_is_a_finding_at_its_section(project):
    project.write_contract("docs", references_contract(required=False))
    write(
        project,
        "docs/guide.md",
        "# Guide\n\n## Install\n\nSee [a](a.md) and [b](b.md).\n\n## Use\n\n[c](c.md)\n",
    )

    result = project.review()

    assert result["outcome"] == "done"  # optional: should-fix, not a blocker
    assert len(broken(result)) == 3
    listed = project.ok("finding", "list")["findings"]
    labels = sorted(f["label"] for f in listed if f.get("criterion") == "DOCS-1")
    assert len(labels) == 2  # one finding per paragraph, both links of one share it
    assert any("install" in label.lower() for label in labels), labels

    write(project, "docs/a.md", "# A\n")
    write(project, "docs/b.md", "# B\n")
    write(project, "docs/c.md", "# C\n")
    again = project.review()
    assert again["should_fix"] == []
    states = {
        f["state"]
        for f in project.ok("finding", "list")["findings"]
        if f.get("criterion") == "DOCS-1"
    }
    assert states == {"closed"}


def test_an_empty_scope_fails_and_a_scope_without_markdown_has_nothing_to_check(
    project,
):
    project.write_contract("docs", references_contract())

    [blocker] = project.review()["blockers"]
    assert blocker["evidence"]["files_checked"] == 0
    assert "no files in scope" in blocker["evidence"]["problem"]

    write(project, "docs/guide.rst", "See `missing <missing.rst>`_.\n")
    round_id = project.ok("round", "start")["round"]
    [result] = project.ok("round", "check", "--round", round_id)["results"]
    assert result["passed"] is True
    assert project.ok("round", "close", "--round", round_id)["outcome"] == "done"
    evidence = result["evidence"]
    assert evidence["files_checked"] == 0
    assert "no Markdown or LaTeX files" in evidence["note"]


def test_a_markdown_file_that_is_not_utf8_fails_the_criterion(project):
    project.write_contract("docs", references_contract())
    write(project, "docs/ok.md", "# Ok\n")
    (project.root / "docs" / "latin1.md").write_bytes("# Caf\xe9\n".encode("latin-1"))

    [blocker] = project.review()["blockers"]

    assert blocker["evidence"]["unreadable"] == [
        {"file": "docs/latin1.md", "reason": "not UTF-8"}
    ]
    assert "could not be read" in blocker["evidence"]["problem"]


def test_targets_outside_the_project_or_spelled_differently_are_broken(project):
    project.write_contract("docs", references_contract())
    write(project, "docs/setup.md", "# Setup\n")
    write(
        project,
        "docs/guide.md",
        "[up](../../outside.md) [case](Setup.md) [root](/docs/setup.md)\n"
        "[dir](../docs/) [space](my%20notes.md)\n",
    )
    write(project, "docs/my notes.md", "# Notes\n")

    result = project.review()

    assert broken(result) == [
        ("docs/guide.md", 1, "../../outside.md", "link"),
        ("docs/guide.md", 1, "Setup.md", "link"),
    ]
    reasons = [b["reason"] for b in result["blockers"][0]["evidence"]["broken"]]
    assert reasons == ["points outside the project", "docs/Setup.md does not exist"]
