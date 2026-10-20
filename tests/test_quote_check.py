"""Reviewer quotes must read at the cited line, normalized for whitespace only."""

import json

import pytest
from conftest import judgment_contract

SAMPLE = (
    "def total(items):\n"
    "    result = 0\n"
    "    for item in items:\n"
    "        result += item.price\n"
    "    return result\n"
    "\n"
    "\n"
    "def label(item):\n"
    '    return f"{item.name}: {item.price}"\n'
    "\n"
    "\n"
    "def count(items):\n"
    "    result = 0\n"
    "    return len(items)\n"
)


def setup(project, text: str = SAMPLE, name: str = "src/cart.py") -> None:
    project.write_contract("code", judgment_contract())
    path = project.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def verdict_output(line: int, quote: str, file: str = "src/cart.py") -> dict:
    place = {"file": file, "line": line, "quote": quote}
    return {
        "criteria": [
            {
                "id": "JUD-1",
                "passed": False,
                "reason": "total() of an empty cart is fine, but see the place.",
                "evidence": [place],
            }
        ],
        "findings": [],
    }


def finding_output(line: int, quote: str, file: str = "src/cart.py") -> dict:
    return {
        "criteria": [
            {
                "id": "JUD-1",
                "passed": True,
                "reason": "Checked the functions in scope.",
                "evidence": [],
            }
        ],
        "findings": [
            {
                "category": "clarity",
                "severity": "note",
                "file": file,
                "line": line,
                "quote": quote,
                "summary": "The name could be clearer.",
            }
        ],
    }


OUTPUTS = {"verdict": verdict_output, "finding": finding_output}


def submit(project, output: dict) -> tuple[int, dict]:
    round_id, runs = project.judged_round("--count", "1")
    return project.submit(round_id, runs[0], json.dumps(output))


def problems(payload: dict) -> list[dict]:
    assert payload["error"]["code"] == "invalid_reviewer_output", payload
    return [p for p in payload["error"]["details"]["problems"] if "quote" in p["field"]]


@pytest.mark.parametrize("kind", list(OUTPUTS))
@pytest.mark.parametrize(
    ("line", "quote"),
    [
        (4, "result += item.price"),  # part of the line
        (4, "        result += item.price"),  # the whole line with its indentation
        (3, "for item in items:\n    result += item.price"),  # several lines
        (3, "for item in items:\n        result += item.price\n    return result"),
        (9, 'return f"{item.name}:   {item.price}"'),  # more spaces than the file
        (3, "for item in items: result += item.price"),  # line break as a space
        (5, "return result\n\n\ndef label(item):"),  # across empty lines
    ],
)
def test_a_quote_at_the_cited_line_is_accepted(project, kind, line, quote):
    setup(project)

    code, payload = submit(project, OUTPUTS[kind](line, quote))

    assert code == 0, payload


@pytest.mark.parametrize("kind", list(OUTPUTS))
def test_a_quote_one_line_off_is_rejected_with_the_line_it_is_on(project, kind):
    setup(project)

    code, payload = submit(project, OUTPUTS[kind](3, "result += item.price"))

    assert code != 0
    [problem] = problems(payload)
    assert "not at line 3 of src/cart.py" in problem["problem"]
    assert "starts at line 4" in problem["problem"]
    assert "reviewer_output" not in [e["type"] for e in project.ledger_events()]


@pytest.mark.parametrize("kind", list(OUTPUTS))
def test_a_quote_far_from_the_cited_line_is_rejected(project, kind):
    setup(project)

    code, payload = submit(project, OUTPUTS[kind](13, "return len(items)"))
    far, far_payload = submit(project, OUTPUTS[kind](1, "return len(items)"))

    assert code != 0
    assert "starts at line 14" in problems(payload)[0]["problem"]
    assert far != 0
    assert "starts at line 14" in problems(far_payload)[0]["problem"]


def test_a_quote_on_several_lines_names_the_nearest_ones(project):
    setup(project)

    code, payload = submit(project, verdict_output(12, "result = 0"))

    assert code != 0
    assert "starts at lines 13 and 2" in problems(payload)[0]["problem"]


@pytest.mark.parametrize("kind", list(OUTPUTS))
@pytest.mark.parametrize(
    ("line", "quote"),
    [
        (4, "result += item.cost"),  # invented
        (4, "Result += item.price"),  # other case
        (4, "result+=item.price"),  # missing spaces are not only whitespace
        (9, "return f'{item.name}: {item.price}'"),  # other quote characters
        (3, "for item in items:\n    return result"),  # lines that do not follow
        (5, "return result\ndef count(items):"),  # a line left out in between
    ],
)
def test_a_quote_that_is_nowhere_in_the_file_is_rejected(project, kind, line, quote):
    setup(project)

    code, payload = submit(project, OUTPUTS[kind](line, quote))

    assert code != 0
    [problem] = problems(payload)
    assert "not found in src/cart.py" in problem["problem"]
    assert "verbatim" in problem["problem"]


@pytest.mark.parametrize("kind", list(OUTPUTS))
def test_a_quote_without_text_is_rejected(project, kind):
    setup(project)

    code, payload = submit(project, OUTPUTS[kind](6, " \n\t"))

    assert code != 0
    [problem] = problems(payload)
    assert "must not be empty" in problem["problem"]


def test_a_quote_that_starts_at_the_line_and_runs_past_the_end_is_rejected(project):
    setup(project)

    code, payload = submit(project, verdict_output(14, "return len(items)\nprint()"))

    assert code != 0
    assert "not found in src/cart.py" in problems(payload)[0]["problem"]


def test_windows_line_endings_and_tabs_are_whitespace(project):
    setup(project, SAMPLE.replace("\n", "\r\n").replace("    ", "\t"))

    code, payload = submit(
        project, verdict_output(3, "for item in items:\n    result += item.price")
    )

    assert code == 0, payload


def test_a_finding_keeps_the_cited_line(project):
    setup(project)
    round_id, runs = project.judged_round("--count", "1")
    output = finding_output(9, 'return f"{item.name}: {item.price}"')

    code, payload = project.submit(round_id, runs[0], json.dumps(output))
    [entry] = project.ok("review", "findings", "--round", round_id)["findings"]

    assert code == 0, payload
    assert entry["line"] == 9
    assert entry["address"]["symbol"] == "label"


def test_lines_are_counted_at_line_breaks_only(project):
    # a form feed or a Unicode line separator does not start a new line for the
    # reviewer's Read tool, so it must not for the check either
    text = 'NOTE = "a b"\n\x0c\ndef first():\n    return 1\n'
    setup(project, text)
    round_id, runs = project.judged_round("--count", "1")
    output = finding_output(4, "return 1")

    code, payload = project.submit(round_id, runs[0], json.dumps(output))
    [entry] = project.ok("review", "findings", "--round", round_id)["findings"]

    assert code == 0, payload
    assert entry["address"]["symbol"] == "first"


def test_a_quote_on_many_lines_names_the_three_nearest(project):
    setup(project)

    code, payload = submit(project, verdict_output(12, "result"))

    assert code != 0
    assert "starts at lines 13, 5, 4 and 1 more" in problems(payload)[0]["problem"]
