"""Unit tests for tool output JSON Schema validation."""

import pytest

from app.agent.registry import ToolRegistry
from app.agent.schema_validation import validate_against_schema


def test_valid_object_passes():
    ok, _ = validate_against_schema(
        {"name": "DocMind", "count": 3},
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "count": {"type": "integer"},
            },
            "required": ["name"],
        },
    )
    assert ok is True


def test_missing_required_property_fails():
    ok, reason = validate_against_schema(
        {"count": 3},
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )
    assert ok is False
    assert "missing required property" in reason


def test_type_mismatch_fails():
    ok, reason = validate_against_schema(
        "hello",
        {"type": "integer"},
    )
    assert ok is False
    assert "expected type" in reason


def test_array_items_validation():
    ok, reason = validate_against_schema(
        [1, 2, "x"],
        {"type": "array", "items": {"type": "integer"}},
    )
    assert ok is False
    assert "item 2" in reason


def test_enum_and_additional_properties():
    ok, reason = validate_against_schema(
        {"status": "unknown", "extra": 1},
        {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["ok", "error"]}},
            "additionalProperties": False,
        },
    )
    assert ok is False
    assert "unexpected properties" in reason or "enum" in reason


@pytest.mark.asyncio
async def test_registry_rejects_output_schema_mismatch():
    registry = ToolRegistry()

    async def dummy_handler(value: int, **_ctx):
        return {"value": value}

    registry.register(
        name="test_tool",
        description="test",
        parameters={"type": "object", "properties": {"value": {"type": "integer"}}},
        handler=dummy_handler,
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    )

    result = await registry.execute_detailed("test_tool", {"value": 1})

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "validation_error"
