
from execution.executor import Executor
from execution.tool_registry import ToolRegistry


def add_numbers(a: int, b: int) -> int:
    return a + b


def dangerous_operation() -> str:
    return "operation executed"


def failing_tool() -> None:
    raise RuntimeError("Sensitive internal detail")


def setup_executor() -> Executor:
    registry = ToolRegistry()
    registry.register("add", add_numbers)
    registry.register("dangerous", dangerous_operation)
    registry.register("failing", failing_tool)
    return Executor(registry)



def test_successful_execution():
    executor = setup_executor()
    arguments = {"a": 2, "b": 3}

    token = executor.approval_manager.issue("add", arguments)
    result = executor.execute(
        "add",
        arguments,
        approval_token=token,
    )

    assert result.success is True
    assert result.output == 5



def test_unknown_tool_returns_error():
    executor = setup_executor()

    result = executor.execute("missing")

    assert result.success is False
    assert result.error_code == "TOOL_NOT_FOUND"


def test_missing_required_argument_returns_error():
    executor = setup_executor()

    result = executor.execute("add", {"a": 2})

    assert result.success is False
    assert result.error_code == "INVALID_ARGUMENTS"


def test_approval_is_required():
    executor = setup_executor()

    result = executor.execute(
        "dangerous",
        requires_approval=True,
    )

    assert result.success is False
    assert result.error_code == "APPROVAL_REQUIRED"
    assert result.output is None



def test_action_bound_approval_executes_tool():
    registry = ToolRegistry()
    registry.register("dangerous", lambda value: f"executed {value}")

    executor = Executor(registry)
    arguments = {"value": "approved action"}

    token = executor.approval_manager.issue("dangerous", arguments)

    result = executor.execute(
        "dangerous",
        arguments,
        requires_approval=True,
        approval_token=token,
    )

    assert result.success is True
    assert result.output == "executed approved action"


def test_legacy_approved_boolean_does_not_authorize():
    registry = ToolRegistry()
    registry.register("dangerous", lambda: "executed")

    executor = Executor(registry)

    result = executor.execute(
        "dangerous",
        requires_approval=True,
        approved=True,
    )

    assert result.success is False
    assert result.error_code == "APPROVAL_REQUIRED"




def test_internal_exception_is_hidden():
    executor = setup_executor()
    token = executor.approval_manager.issue("failing", {})

    result = executor.execute(
        "failing",
        approval_token=token,
    )

    assert result.success is False
    assert result.error_code == "INTERNAL_ERROR"
    assert result.error_message == "The tool failed during execution."



def test_invalid_arguments_type():
    executor = setup_executor()

    result = executor.execute("add", ["not", "a", "dictionary"])

    assert result.success is False
    assert result.error_code == "INVALID_ARGUMENTS"
    assert result.verified is False



def test_rejected_request_is_not_marked_verified():
    executor = setup_executor()

    result = executor.execute("missing")

    assert result.success is False
    assert result.error_code == "TOOL_NOT_FOUND"
    assert result.verified is False



def test_wrong_action_token_does_not_execute_tool():
    registry = ToolRegistry()
    calls = []
    registry.register("dangerous", lambda value: calls.append(value))

    executor = Executor(registry)
    token = executor.approval_manager.issue(
        "dangerous", {"value": "original"}
    )

    result = executor.execute(
        "dangerous",
        {"value": "changed"},
        requires_approval=True,
        approval_token=token,
    )

    assert result.success is False
    assert result.error_code == "APPROVAL_INVALID"
    assert calls == []


def test_unknown_token_does_not_execute_tool():
    registry = ToolRegistry()
    calls = []
    registry.register("dangerous", lambda: calls.append("executed"))

    executor = Executor(registry)

    result = executor.execute(
        "dangerous",
        requires_approval=True,
        approval_token="not-a-real-token",
    )

    assert result.success is False
    assert result.error_code == "APPROVAL_INVALID"
    assert calls == []