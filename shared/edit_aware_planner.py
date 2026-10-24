
"""Route supported file-edit requests through PHANES approval."""
from __future__ import annotations

import re
from uuid import uuid4

from execution.filesystem import FilesystemTools
from shared.contracts import Plan, PlanStep
from shared.llm.edit_planner import EditPlanner, EditProposalError
from shared.read_only_planner import ReadOnlyPlanner, UnsupportedRequestError


class EditAwarePlanner:
    """Support approved edits while preserving read-only planning."""

    def __init__(
        self,
        edit_planner: EditPlanner | None = None,
        read_only_planner: ReadOnlyPlanner | None = None,
    ) -> None:
        self.edit_planner = edit_planner or EditPlanner()
        self.read_only_planner = read_only_planner or ReadOnlyPlanner()

    def __call__(self, goal: str, workspace: str, context: object) -> Plan:
        if not isinstance(goal, str) or not goal.strip():
            raise UnsupportedRequestError("Enter a task to continue.")

        text = goal.strip()
        lower = text.lower()

        edit_intent = re.search(
            r"\b(fix|edit|modify|update|change|correct|repair)\b",
            lower,
        )

        if not edit_intent:
            return self.read_only_planner(goal, workspace, context)

        path_match = re.search(
            r"""(?<![\w.-])((?:[\w.-]+[\\/])*[\w.-]+\.[A-Za-z0-9_+-]+)""",
            text,
        )
        if not path_match:
            raise UnsupportedRequestError(
                "Please name the workspace file you want to edit."
            )

        requested_path = self.edit_planner.validate_relative_path(
            path_match.group(1)
        )

        # FilesystemTools confines this read to the selected workspace.
        filesystem = FilesystemTools(workspace)
        original = filesystem.read_file(requested_path)

        proposal = self.edit_planner.propose(
            goal=text,
            path=requested_path,
            original_content=original,
        )

        edit_id = uuid4().hex
        write_step_id = f"apply_edit_{edit_id}"
        read_step_id = f"verify_edit_{edit_id}"

        return Plan(
            task_id="planner-generated",
            rationale=(
                f"Propose and obtain approval before modifying "
                f"{proposal.path}. {proposal.explanation}"
            ),
            steps=[
                PlanStep(
                    step_id=write_step_id,
                    tool="filesystem.write_file",
                    arguments={
                        "path": proposal.path,
                        "content": proposal.proposed_content,
                    },
                    requires_approval=True,
                    description=(
                        f"Review the proposed replacement for {proposal.path}. "
                        f"{proposal.explanation}"
                    ),
                ),
                PlanStep(
                    step_id=read_step_id,
                    tool="filesystem.read_file",
                    arguments={"path": proposal.path},
                    depends_on=[write_step_id],
                    description=(
                        f"Read {proposal.path} again after the approved edit."
                    ),
                ),
            ],
        )
