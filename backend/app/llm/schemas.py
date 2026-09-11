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


def _allows_null(schema: dict[str, Any] | None) -> bool:
    """Does this subschema permit null? An undeclared type permits anything."""
    if not isinstance(schema, dict) or "type" not in schema:
        return True
    declared = schema["type"]
    return "null" in (declared if isinstance(declared, list) else [declared])


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
            elif data[key] is None and not _allows_null(properties.get(key)):
                # Present but null. Strict structured output makes the model
                # name every property, so this is now a shape a model can
                # actually produce, and silently accepting it would hand the
                # caller a None where the schema promised a value.
                errors.append(f"{path}: required property '{key}' is null")
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


# --- strict structured output ---------------------------------------------
#
# Endpoints that enforce a schema by constrained decoding (Groq, OpenAI's
# strict mode) accept a much smaller dialect than the one above. Meeting it is
# worth some translation: the shape stops being a request to the model and
# becomes a property of the decoder, which is one round trip instead of three
# and one fewer thing a hostile page can talk the model out of.
#
# The original schema stays the gate. This only changes what goes on the wire.

# Rejected outright by strict mode. Dropping them loses nothing, because
# `validate_against_schema` still applies them to whatever comes back.
_UNSUPPORTED_KEYWORDS = frozenset(
    {"minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum",
     "exclusiveMaximum", "multipleOf", "pattern", "format", "default"}
)


class _Undeclarable(Exception):
    """This schema has no exact strict equivalent."""


def _strictify(node: Any, *, nullable: bool) -> Any:
    if not isinstance(node, dict):
        return node

    out = {k: v for k, v in node.items() if k not in _UNSUPPORTED_KEYWORDS}
    node_type = out.get("type")

    if node_type == "object":
        properties = out.get("properties")
        if not properties:
            # An open map. Strict mode can only describe closed objects, and
            # inventing a shape for this one would constrain the model to
            # fields nobody declared.
            raise _Undeclarable("object without properties")

        required = set(out.get("required", []))
        out["properties"] = {
            name: _strictify(sub, nullable=name not in required)
            for name, sub in properties.items()
        }
        # Every property must be listed, so optionality moves into the type.
        out["required"] = list(properties)
        out["additionalProperties"] = False

    elif node_type == "array" and "items" in out:
        out["items"] = _strictify(out["items"], nullable=False)

    if nullable and node_type is not None:
        out["type"] = [node_type, "null"] if isinstance(node_type, str) else [*node_type, "null"]
        if "enum" in out and None not in out["enum"]:
            # Nullable and enumerated otherwise contradict: null fails the
            # enum, and every enum member asserts a choice never made.
            out["enum"] = [*out["enum"], None]

    return out


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    """The strict-mode equivalent of `schema`, or None if there isn't one.

    Never mutates the input: these schemas are module-level constants shared
    by every request in the process, and corrupting one in place would break
    every later call while the first looked fine.
    """
    try:
        return _strictify(json.loads(json.dumps(schema)), nullable=False)
    except _Undeclarable:
        return None


def strip_optional_nulls(data: Any, schema: dict[str, Any]) -> Any:
    """Drop the nulls strict mode forced the model to write.

    Strict mode requires every property to be mentioned, so one the model chose
    to omit arrives as an explicit null. Callers were written against the
    original schema and expect it absent, so this restores that shape and the
    contract stays identical whichever mode produced the object.

    A null in a *required* property is left alone: it is a schema violation,
    and removing it would turn a clear validation error into a missing key.
    """
    if not isinstance(data, dict) or not isinstance(schema, dict):
        return data

    properties: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))
    cleaned: dict[str, Any] = {}

    for key, value in data.items():
        if value is None and key not in required:
            continue
        subschema = properties.get(key)
        if isinstance(subschema, dict) and value is not None:
            if subschema.get("type") == "object":
                value = strip_optional_nulls(value, subschema)
            elif subschema.get("type") == "array" and isinstance(value, list):
                items = subschema.get("items", {})
                value = [strip_optional_nulls(item, items) for item in value]
        cleaned[key] = value

    return cleaned
