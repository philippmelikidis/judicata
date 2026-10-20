"""Judgment: the criterion text is answered by the reviewers. No parameters."""


def validate(params: dict) -> tuple[dict, list[tuple[str, str]]]:
    return {}, [(key, "unknown field") for key in sorted(params)]


PENDING_REASON = "judged by reviewers"
