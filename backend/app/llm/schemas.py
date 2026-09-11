"""Structured-output helpers: extracting and checking JSON from model text.

Small local models frequently wrap JSON in prose or fences, emit trailing
commas, or use single quotes. `extract_json` recovers the object where it
safely can, and `validate_against_schema` applies a deliberately small subset of
JSON Schema so a malformed object is rejected here rather than deeper in the
agent loop.

Recovery never *invents* fields -- it only repairs syntax. Anything that cannot
be parsed is an error, not a guess.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")
# Some local models emit reasoning inside <think> blocks before the answer.
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class JSONExtractionError(ValueError):
    """No parseable JSON object could be recovered from the text."""


def _balanced_object(text: str) -> str | None:
    """Return the first brace-balanced object, ignoring braces inside strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of a single JSON object from model output."""
    if not text or not text.strip():
        raise JSONExtractionError("Model returned an empty response")

    cleaned = _THINK_BLOCK.sub("", text).strip()

    candidates: list[str] = []
    fenced = _FENCE.findall(cleaned)
    candidates.extend(block.strip() for block in fenced)
    candidates.append(cleaned)
    balanced = _balanced_object(cleaned)
    if balanced:
        candidates.append(balanced)

    for candidate in candidates:
        if not candidate:
            continue
        for attempt in (candidate, _TRAILING_COMMA.sub(r"\1", candidate)):
            try:
                parsed = json.loads(attempt)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                # A model that wrapped its single object in an array.
                return parsed[0]

    raise JSONExtractionError(
        f"No JSON object found in model output (first 200 chars: {cleaned[:200]!r})"
    )


_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "null": (type(None),),
}


def _type_ok(value: Any, expected: str) -> bool:
    allowed = _TYPE_MAP.get(expected)
    if allowed is None:
        return True
    if expected in ("number", "integer") and isinstance(value, bool):
        # bool is a subclass of int; treat it as a distinct type.
        return False
    return isinstance(value, allowed)


def validate_against_schema(data: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate `data` against a JSON Schema subset.

    Supported: type, required, properties, additionalProperties, enum, items,
    minimum/maximum, minLength/maxLength. Returns a list of human-readable
    errors -- empty means valid.
    """
    errors: list[str] = []

    expected_type = schema.get("type")
    if expected_type:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_type_ok(data, t) for t in types):
            got = type(data).__name__
            errors.append(f"{path}: expected {'/'.join(types)}, got {got}")
            return errors

    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{path}: value {data!r} is not one of {schema['enum']}")

    if isinstance(data, dict):
        properties: dict[str, Any] = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in data:
                errors.append(f"{path}: missing required property '{key}'")
        if schema.get("additionalProperties") is False:
            for key in data:
                if key not in properties:
                    errors.append(f"{path}: unexpected property '{key}'")
        for key, subschema in properties.items():
            if key in data and data[key] is not None:
                errors.extend(validate_against_schema(data[key], subschema, f"{path}.{key}"))

    if isinstance(data, list) and "items" in schema:
        for i, item in enumerate(data):
            errors.extend(validate_against_schema(item, schema["items"], f"{path}[{i}]"))

    if isinstance(data, str):
        if "minLength" in schema and len(data) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(data) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")

    if isinstance(data, int | float) and not isinstance(data, bool):
        if "minimum" in schema and data < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and data > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")

    return errors
