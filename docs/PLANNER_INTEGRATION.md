# PHANES planner integration notes

## Canonical shared types

Planner code imports `Plan`, `PlanStep`, `ToolResult`, `ToolError`, `ExecutionStatus`, and `ErrorCode` from `shared.contracts`, and `TaskContext` from `shared.context`. It does not define competing versions of these contracts.

## Planning strategy

`Planner` takes an injected `plan_provider` callable. The provider must accept keyword arguments `goal`, `workspace_id`, `context`, and `task_id`, and return a canonical `Plan`. This keeps the planner independent of a specific LLM SDK and avoids assuming a tool catalogue that the shared contracts do not publish. Pass `allowed_tools` when the orchestrator/tool registry can supply the real tool names.

The orchestrator should pass its already-persisted task ID to `create_plan`. If it omits `task_id`, the planner generates a UUID.

## Plan validation and dependencies

`validate_plan` checks non-empty IDs and tool names, duplicate IDs, unknown dependency IDs, self-dependencies, duplicate dependency entries, and cycles. Tool names are checked only when an actual registry catalogue is provided. `eligible_steps` is a pure helper; it never runs tools or writes storage.

## Result evaluation

`Replanner.evaluate_result` returns a planner-local `ReplanDecision`. The shared contract currently has no canonical planner-decision model or planner service protocol, so the orchestrator must adapt this local decision type. This is a concrete integration point to agree on, not a reason to duplicate shared execution schemas.

Uncertain statuses (`UNKNOWN`, `TIMED_OUT`) and uncertain error codes (`OUTCOME_UNKNOWN`, `EXECUTION_TIMEOUT`, `VERIFICATION_FAILED`) return `NEEDS_ATTENTION`, never `RETRY`. A retry requires both `ToolError.retryable=True` and `ToolError.details["retry_safe"] is True`, and is bounded by `max_retries` (default 2 retries after the initial attempt). The `retry_safe` details key is a convention using an existing field, not a new shared field; the team should standardize its meaning across executor and planner.

A successful `ToolResult` means the tool operation succeeded; it does not by itself prove `verified=True`. The planner does not upgrade verification, enforce authorization, execute tools, access SQLite, or assume cancellation stopped a running process.
