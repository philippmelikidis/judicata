"""A small JSON Schema validator for the subset the core's own schemas use.

Supported keywords: type, properties, required, additionalProperties (false),
items, minItems, maxItems, minLength, maxLength, minimum, maximum, enum and
$ref to "#/$defs/<name>". minLength counts without surrounding whitespace, so
minLength 1 means "not blank". Anything else in a schema is a bug in the core,
not in the input, so it raises instead of being ignored.
"""

SUPPORTED = {
    "$schema",
    "$id",
    "$defs",
    "$ref",
    "title",
    "description",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "enum",
}

TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate(value, schema: dict) -> list[dict]:
    """All problems as [{"field", "problem"}]; an empty list means valid."""
    problems: list[dict] = []
    _check(value, schema, schema, "", problems)
    return problems


def _check(value, schema: dict, root: dict, where: str, problems: list) -> None:
    unknown = set(schema) - SUPPORTED
    if unknown:
        raise ValueError(f"unsupported schema keywords: {sorted(unknown)}")
    if "$ref" in schema:
        name = schema["$ref"].removeprefix("#/$defs/")
        _check(value, root["$defs"][name], root, where, problems)
        return

    def problem(text: str) -> None:
        problems.append({"field": where or "(root)", "problem": text})

    kind = schema.get("type")
    if kind is not None and not TYPES[kind](value):
        problem(f"must be {'an' if kind[0] in 'aeiou' else 'a'} {kind}")
        return
    if "enum" in schema and value not in schema["enum"]:
        problem(f"must be one of {', '.join(map(str, schema['enum']))}")
    if isinstance(value, str):
        if len(value.strip()) < schema.get("minLength", 0):
            problem("must not be empty")
        if len(value) > schema.get("maxLength", len(value)):
            problem(f"must be at most {schema['maxLength']} characters")
    if TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            problem(f"must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problem(f"must be at most {schema['maximum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            problem(f"needs at least {schema['minItems']} entries")
        if len(value) > schema.get("maxItems", len(value)):
            problem(f"may have at most {schema['maxItems']} entries")
        if "items" in schema:
            for index, item in enumerate(value):
                _check(item, schema["items"], root, f"{where}[{index}]", problems)
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                problems.append({"field": _join(where, key), "problem": "required"})
        for key, item in value.items():
            if key in properties:
                _check(item, properties[key], root, _join(where, key), problems)
            elif schema.get("additionalProperties") is False:
                problems.append(
                    {"field": _join(where, key), "problem": "unknown field"}
                )


def _join(where: str, key: str) -> str:
    return f"{where}.{key}" if where else key
