
import pytest

from execution.tool_registry import (
    DuplicateToolError,
    ToolNotFoundError,
    ToolRegistry,
)


def sample_tool(value: str) -> str:
    return f"Processed: {value}"


def test_register_and_retrieve_tool():
    registry = ToolRegistry()

    registry.register("sample", sample_tool)

    retrieved_tool = registry.get("sample")

    assert retrieved_tool is sample_tool
    assert retrieved_tool("hello") == "Processed: hello"


def test_duplicate_registration_is_rejected():
    registry = ToolRegistry()
    registry.register("sample", sample_tool)

    with pytest.raises(DuplicateToolError):
        registry.register("sample", sample_tool)


def test_unknown_tool_raises_error():
    registry = ToolRegistry()

    with pytest.raises(ToolNotFoundError):
        registry.get("missing_tool")


def test_empty_tool_name_is_rejected():
    registry = ToolRegistry()

    with pytest.raises(ValueError):
        registry.register("   ", sample_tool)


def test_non_callable_tool_is_rejected():
    registry = ToolRegistry()

    with pytest.raises(TypeError):
        registry.register("invalid", "not a function")


def test_list_tools():
    registry = ToolRegistry()
    registry.register("first", sample_tool)
    registry.register("second", sample_tool)

    assert registry.list_tools() == ["first", "second"]


def test_contains_and_length():
    registry = ToolRegistry()
    registry.register("sample", sample_tool)

    assert "sample" in registry
    assert "missing" not in registry
    assert len(registry) == 1


def test_tool_name_whitespace_is_trimmed():
    registry = ToolRegistry()
    registry.register("  sample  ", sample_tool)

    assert registry.get("sample") is sample_tool
