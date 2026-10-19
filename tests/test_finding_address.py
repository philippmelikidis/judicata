"""Findings outside the contract get a normalized address, a section hash and a fingerprint."""

import json

from conftest import judgment_contract, reviewer_output


def finding(**changes) -> dict:
    base = json.loads(reviewer_output("findings"))["findings"][0]
    return {**base, **changes}


def output(*findings) -> str:
    data = json.loads(reviewer_output("findings"))
    data["findings"] = list(findings)
    return json.dumps(data)


def found(project, *findings) -> list[dict]:
    """One round with a single reviewer that reports these findings."""
    round_id, runs = project.judged_round("--count", "1")
    code, payload = project.submit(round_id, runs[0], output(*findings))
    assert code == 0, payload
    return project.ok("review", "findings", "--round", round_id)["findings"]


def one(project, **changes) -> dict:
    [entry] = found(project, finding(**changes))
    return entry


def setup(project):
    project.add_artifact()
    project.write_contract("code", judgment_contract())


AREA = {
    "line": 11,
    "quote": "return math.pi * self.radius**2",
    "symbol": "area",
    "category": "clarity",
    "severity": "note",
}


def test_python_address_is_the_class_and_function_around_the_line(project):
    setup(project)

    entry = one(project, **AREA)

    assert entry["address"] == {
        "file": "src/shapes.py",
        "kind": "python",
        "symbol": "Circle.area",
    }
    assert entry["label"] == "src/shapes.py::Circle.area"


def test_python_address_follows_nested_functions_not_the_reviewers_symbol(project):
    setup(project)

    entry = one(project, line=15, quote="return value * factor", symbol="wrong_name")

    assert entry["address"]["symbol"] == "Circle.scaled.grow"


def test_python_module_level_line_is_addressed_by_the_file(project):
    setup(project)

    entry = one(project, line=3, quote="import math", symbol="math")

    assert entry["address"] == {"file": "src/shapes.py", "kind": "file"}
    assert entry["label"] == "src/shapes.py"


def test_the_line_is_taken_from_the_quote_when_the_reviewer_is_a_little_off(project):
    setup(project)

    entry = one(project, line=18, quote="return a / b")  # the quote is on line 21

    assert entry["address"]["symbol"] == "ratio"


def shapes(project):
    return project.root / "src" / "shapes.py"


def test_python_address_fingerprint_and_hash_survive_shifted_lines(project):
    setup(project)
    before = one(project, **AREA)

    path = shapes(project)
    path.write_text("# a comment\n# another one\n\n" + path.read_text())
    after = one(project, **{**AREA, "line": 14})

    assert after["address"] == before["address"]
    assert after["fingerprint"] == before["fingerprint"]
    assert after["section_hash"] == before["section_hash"]


def test_section_hash_changes_exactly_when_the_function_changes(project):
    setup(project)
    before = one(project, **AREA)

    path = shapes(project)
    path.write_text(
        path.read_text().replace("return a / b", "return a / b if b else 0")
    )
    other_changed = one(project, **AREA)
    path.write_text(
        path.read_text().replace("self.radius**2", "self.radius * self.radius")
    )
    own_changed = one(
        project, **{**AREA, "quote": "return math.pi * self.radius * self.radius"}
    )

    assert other_changed["section_hash"] == before["section_hash"]
    assert own_changed["section_hash"] != before["section_hash"]
    assert own_changed["fingerprint"] == before["fingerprint"]


GUIDE = {"file": "docs/guide.md", "category": "factual", "severity": "note"}


def test_markdown_address_is_the_heading_path_and_the_paragraph_number(project):
    setup(project)

    entry = one(project, **GUIDE, line=9, quote="Then restart Claude Code.")

    assert entry["address"] == {
        "file": "docs/guide.md",
        "kind": "markdown",
        "headings": ["guide", "install"],
        "paragraph": 2,
    }
    assert entry["label"] == "docs/guide.md > Guide > Install, paragraph 2"


def test_markdown_code_fences_are_one_paragraph_and_hold_no_headings(project):
    setup(project)

    fence = one(project, **GUIDE, line=12, quote="# not a heading")
    nested = one(project, **GUIDE, line=18, quote="Install python3 first.")

    assert fence["address"]["headings"] == ["guide", "install"]
    assert fence["address"]["paragraph"] == 3
    assert nested["address"]["headings"] == ["guide", "install", "linux"]
    assert nested["address"]["paragraph"] == 1


def guide(project):
    return project.root / "docs" / "guide.md"


def test_markdown_address_and_hash_survive_changes_in_other_sections(project):
    setup(project)
    place = {**GUIDE, "line": 9, "quote": "Then restart Claude Code."}
    before = one(project, **place)

    path = guide(project)
    path.write_text(
        path.read_text().replace(
            "judicata keeps review decisions in the repository.\n",
            "judicata keeps review decisions in the repository.\n\nA second intro.\n",
        )
    )
    after = one(project, **{**place, "line": 11})

    assert after["address"] == before["address"]
    assert after["section_hash"] == before["section_hash"]
    assert after["fingerprint"] == before["fingerprint"]


def test_markdown_section_hash_changes_when_the_paragraph_changes(project):
    setup(project)
    place = {**GUIDE, "line": 9, "quote": "Then restart Claude Code."}
    before = one(project, **place)

    path = guide(project)
    path.write_text(
        path.read_text().replace("Then restart Claude Code.", "Then restart it.")
    )
    after = one(project, **{**place, "quote": "Then restart it."})

    assert after["fingerprint"] == before["fingerprint"]
    assert after["section_hash"] != before["section_hash"]


CART = {
    "file": "web/cart.js",
    "line": 2,
    "quote": "return items.reduce((sum, item) => sum + item.price, 0);",
    "category": "maintainability",
    "severity": "note",
}


def test_other_languages_use_the_reviewers_symbol_normalized(project):
    setup(project)

    plain = one(project, **CART, symbol="total")
    called = one(project, **CART, symbol=" total() ")

    assert plain["address"] == {
        "file": "web/cart.js",
        "kind": "symbol",
        "symbol": "total",
    }
    assert called["fingerprint"] == plain["fingerprint"]


def test_without_a_symbol_the_address_is_the_file(project):
    setup(project)
    place = {key: value for key, value in CART.items()}

    entry = one(project, **place, symbol="")

    assert entry["address"] == {"file": "web/cart.js", "kind": "file"}


def test_python_that_does_not_parse_falls_back_to_the_symbol(project):
    setup(project)
    broken = project.root / "src" / "broken.py"
    broken.write_text("def half(x:\n    return x / 2\n")

    entry = one(
        project,
        file="src/broken.py",
        line=2,
        quote="return x / 2",
        symbol="half",
    )

    assert entry["address"] == {
        "file": "src/broken.py",
        "kind": "symbol",
        "symbol": "half",
    }


def test_fingerprint_is_stable_across_rounds_and_depends_on_the_category(project):
    setup(project)

    first = one(project, **AREA)
    second = one(project, **AREA)
    other = one(project, **{**AREA, "category": "style"})

    assert first["fingerprint"] == second["fingerprint"]
    assert other["fingerprint"] != first["fingerprint"]
    assert other["address"] == first["address"]


def write(project, relative: str, text: str) -> None:
    path = project.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_repeated_headings_never_collide_with_a_literal_heading(project):
    setup(project)
    write(project, "docs/faq.md", "## A\n\none\n\n## a@2\n\ntwo\n\n## A\n\nthree\n")

    literal = one(project, **{**GUIDE, "file": "docs/faq.md"}, line=7, quote="two")
    repeated = one(project, **{**GUIDE, "file": "docs/faq.md"}, line=11, quote="three")

    assert literal["address"] != repeated["address"]
    assert literal["fingerprint"] != repeated["fingerprint"]


def test_multi_line_setext_heading_is_a_heading(project):
    setup(project)
    write(project, "docs/long.md", "Intro.\n\nFirst line\nsecond line\n---\n\nBody.\n")

    entry = one(project, **{**GUIDE, "file": "docs/long.md"}, line=7, quote="Body.")

    assert entry["address"]["headings"] == ["first line second line"]
    assert entry["address"]["paragraph"] == 1


def test_trailing_spaces_inside_a_function_change_its_hash(project):
    setup(project)
    place = {
        "file": "src/text.py",
        "line": 2,
        "quote": 'return """a',
        "category": "clarity",
        "severity": "note",
    }
    write(project, "src/text.py", 'def f():\n    return """a  \nb"""\n')
    before = one(project, **place)

    write(project, "src/text.py", 'def f():\n    return """a\nb"""\n')
    after = one(project, **place)

    assert after["fingerprint"] == before["fingerprint"]
    assert after["section_hash"] != before["section_hash"]


def test_a_multi_line_quote_anchors_where_the_whole_quote_is(project):
    setup(project)
    write(
        project,
        "src/zero.py",
        "def f():\n    return 0\n\n\ndef g():\n    return 0\n    # detail\n",
    )

    entry = one(
        project,
        file="src/zero.py",
        line=2,
        quote="return 0\n    # detail",
        category="clarity",
        severity="note",
    )

    assert entry["address"]["symbol"] == "g"
