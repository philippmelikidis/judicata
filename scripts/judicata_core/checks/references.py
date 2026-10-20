"""References: links, anchors, images and citation keys in scope must exist.

code_paths = true (default false) also checks paths written in backticks. The
check itself is not part of this version yet: until it is, a references
criterion has no result and stays pending with PENDING_REASON.
"""


def validate(params: dict) -> tuple[dict, list[tuple[str, str]]]:
    problems = [(key, "unknown field") for key in sorted(set(params) - {"code_paths"})]
    code_paths = params.get("code_paths", False)
    if not isinstance(code_paths, bool):
        problems.append(("code_paths", "must be true or false"))
    return {"code_paths": code_paths}, problems


PENDING_REASON = (
    "the references check is not available in this version of judicata yet; "
    "the criterion stays pending"
)
