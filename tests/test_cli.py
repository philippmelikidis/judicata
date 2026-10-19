"""The command line always answers in JSON, also when it is misused."""


def test_unknown_subcommand_is_a_json_usage_error(project):
    code, payload = project.core("round", "explode")
    assert code != 0
    assert payload["ok"] is False
    assert payload["error"]["code"] == "usage"


def test_project_option_is_required(project):
    code, payload = project.run("round", "start")
    assert code != 0
    assert payload["error"]["code"] == "usage"
    assert "--project" in payload["error"]["message"]


def test_missing_project_directory_is_reported(project):
    code, payload = project.run(
        "round", "start", "--project", str(project.root / "missing")
    )
    assert code != 0
    assert payload["error"]["code"] == "project_not_found"
