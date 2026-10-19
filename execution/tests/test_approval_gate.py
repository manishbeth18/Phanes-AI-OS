from datetime import datetime, timedelta, timezone

import pytest

from execution.approval import ApprovalManager
from execution.approval_gate import ApprovalGate, ApprovalGateError
from shared.contracts import ApprovalDecision, ApprovalRequest


def make_request(
    *,
    approval_id="approval-1",
    task_id="task-1",
    step_id="step-1",
    tool="filesystem.write_file",
    arguments=None,
    expires_in=60,
):
    return ApprovalRequest(
        approval_id=approval_id,
        task_id=task_id,
        step_id=step_id,
        tool=tool,
        arguments={"path": "notes.txt", "content": "hello"} if arguments is None else arguments,
        summary="Write notes.txt",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    )


def make_decision(request, approved=True):
    return ApprovalDecision(
        approval_id=request.approval_id,
        task_id=request.task_id,
        step_id=request.step_id,
        approved=approved,
        decided_at=datetime.now(timezone.utc),
    )


def test_approved_matching_decision_issues_action_bound_single_use_token():
    manager = ApprovalManager()
    gate = ApprovalGate(manager)
    request = make_request()
    gate.register_request(request)

    result = gate.resolve(make_decision(request))

    assert result.approved is True
    assert result.token
    assert manager.consume(result.token, request.tool, request.arguments) is True
    assert manager.consume(result.token, request.tool, request.arguments) is False


def test_rejection_does_not_issue_token():
    manager = ApprovalManager()
    gate = ApprovalGate(manager)
    request = make_request()
    gate.register_request(request)

    result = gate.resolve(make_decision(request, approved=False))

    assert result.approved is False
    assert result.token is None
    with pytest.raises(ApprovalGateError):
        gate.resolve(make_decision(request))


@pytest.mark.parametrize(
    "changed",
    [
        {"approval_id": "unknown"},
        {"task_id": "other-task"},
        {"step_id": "other-step"},
    ],
)
def test_mismatched_decision_does_not_issue_token_or_consume_request(changed):
    manager = ApprovalManager()
    gate = ApprovalGate(manager)
    request = make_request()
    gate.register_request(request)
    values = {
        "approval_id": request.approval_id,
        "task_id": request.task_id,
        "step_id": request.step_id,
        "approved": True,
        "decided_at": datetime.now(timezone.utc),
    }
    values.update(changed)

    with pytest.raises(ApprovalGateError):
        gate.resolve(ApprovalDecision(**values))

    # A subsequent correctly correlated decision still works if the ID existed.
    if changed.get("approval_id") != "unknown":
        result = gate.resolve(make_decision(request))
        assert result.token


def test_mutating_original_request_arguments_does_not_change_registered_action():
    manager = ApprovalManager()
    gate = ApprovalGate(manager)
    arguments = {"path": "notes.txt", "content": "original"}
    request = make_request(arguments=arguments)
    gate.register_request(request)
    arguments["content"] = "tampered"

    result = gate.resolve(make_decision(request))

    assert result.token
    assert manager.consume(result.token, request.tool, {"path": "notes.txt", "content": "original"})
    assert not manager.consume(result.token, request.tool, {"path": "notes.txt", "content": "tampered"})


def test_expired_request_is_not_registered():
    gate = ApprovalGate()
    request = make_request(expires_in=-1)
    with pytest.raises(ApprovalGateError):
        gate.register_request(request)


def test_duplicate_approval_id_cannot_replace_pending_request():
    gate = ApprovalGate()
    first = make_request(arguments={"path": "a.txt"})
    second = make_request(arguments={"path": "b.txt"})
    gate.register_request(first)
    with pytest.raises(ApprovalGateError):
        gate.register_request(second)

    result = gate.resolve(make_decision(first))
    assert result.token


def test_naive_expiry_and_decision_timestamps_are_rejected():
    gate = ApprovalGate()
    request = make_request()
    request = ApprovalRequest(
        approval_id=request.approval_id,
        task_id=request.task_id,
        step_id=request.step_id,
        tool=request.tool,
        arguments=request.arguments,
        summary=request.summary,
        expires_at=datetime.now() + timedelta(seconds=60),
    )
    with pytest.raises(ApprovalGateError):
        gate.register_request(request)

    request = make_request()
    gate.register_request(request)
    decision = ApprovalDecision(
        approval_id=request.approval_id,
        task_id=request.task_id,
        step_id=request.step_id,
        approved=True,
        decided_at=datetime.now(),
    )
    with pytest.raises(ApprovalGateError):
        gate.resolve(decision)


def test_invalid_decision_does_not_issue_token():
    gate = ApprovalGate()
    request = make_request()
    gate.register_request(request)
    malformed = ApprovalDecision(
        approval_id=request.approval_id,
        task_id=request.task_id,
        step_id=request.step_id,
        approved=1,  # bool is required, not an integer
        decided_at=datetime.now(timezone.utc),
    )
    with pytest.raises(ApprovalGateError):
        gate.resolve(malformed)

    result = gate.resolve(make_decision(request))
    assert result.token


def test_concurrent_decisions_issue_at_most_one_token():
    from concurrent.futures import ThreadPoolExecutor

    gate = ApprovalGate()
    request = make_request()
    gate.register_request(request)
    decision = make_decision(request)

    def resolve_once():
        try:
            return gate.resolve(decision)
        except ApprovalGateError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: resolve_once(), range(8)))

    issued = [result for result in results if result is not None and result.token]
    assert len(issued) == 1
