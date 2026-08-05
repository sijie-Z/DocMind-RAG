"""Lightweight JSON Schema validation for tool outputs.

This is intentionally small: it covers the common JSON Schema subset used by
DocMind tool ``output_schema`` declarations (types, required fields, enums,
length/range limits, arrays, and ``anyOf``).
"""

from typing import Any


def validate_against_schema(value: Any, schema: dict[str, Any]) -> tuple[bool, str]:
    """Validate ``value`` against a JSON Schema subset.

    Returns ``(True, "")`` on success or ``(False, reason)`` on failure.
    """
    if not isinstance(schema, dict):
        return True, ""

    if "anyOf" in schema:
        for option in schema["anyOf"]:
            ok, _ = validate_against_schema(value, option)
            if ok:
                return True, ""
        return False, f"value does not match anyOf: {value!r}"

    expected = schema.get("type")
    if expected is not None and not _matches_type(value, expected):
        return False, f"expected type '{expected}', got {type(value).__name__}"

    if expected == "object" and isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                return False, f"missing required property '{key}'"
        if schema.get("additionalProperties") is False:
            allowed = set(schema.get("properties", {}).keys())
            unknown = [k for k in value if k not in allowed]
            if unknown:
                return False, f"unexpected properties: {', '.join(unknown)}"
        for key, prop_schema in schema.get("properties", {}).items():
            if key in value:
                ok, reason = validate_against_schema(value[key], prop_schema)
                if not ok:
                    return False, f"property '{key}': {reason}"
    elif expected == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                ok, reason = validate_against_schema(item, item_schema)
                if not ok:
                    return False, f"item {index}: {reason}"
        if "minItems" in schema and len(value) < schema["minItems"]:
            return False, f"array shorter than minItems={schema['minItems']}"
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            return False, f"array longer than maxItems={schema['maxItems']}"
    elif expected == "string" and isinstance(value, str):
        if "enum" in schema and value not in schema["enum"]:
            return False, f"value not in enum: {value!r}"
        if "minLength" in schema and len(value) < schema["minLength"]:
            return False, f"string shorter than minLength={schema['minLength']}"
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            return False, f"string longer than maxLength={schema['maxLength']}"
    elif expected in ("number", "integer") and isinstance(value, (int, float)):
        if expected == "integer" and not isinstance(value, int):
            return False, "expected integer, got float"
        if "minimum" in schema and value < schema["minimum"]:
            return False, f"value below minimum={schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return False, f"value above maximum={schema['maximum']}"

    return True, ""


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True
