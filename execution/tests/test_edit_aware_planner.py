
from pathlib import Path

from shared.contracts import Plan
from shared.edit_aware_planner import EditAwarePlanner
from shared.llm.edit_planner import EditPlanner
from shared.llm.provider_manager import LLMResponse


class FakeProviderManager:
    def generate(self, prompt):
        return LLMResponse(
            text=(
                '{"proposed_content": "print(\'fixed\')\\n", '
                '"explanation": "Fix the requested file.", '
                '"verification_steps": ["Read the file again."]}'
            ),
            provider="fake",
            model="test",
        )


def test_edit_request_creates_approval_required_write_and_readback(tmp_path):
    (tmp_path / "debug_demo.py").write_text(
        "print('original')\n",
        encoding="utf-8",
    )
    planner = EditAwarePlanner(
        edit_planner=EditPlanner(FakeProviderManager())
    )

    plan = planner(
        "Fix the bug in debug_demo.py",
        str(tmp_path),
        {},
    )

    assert isinstance(plan, Plan)
    assert len(plan.steps) == 2

    write_step, read_step = plan.steps
    assert write_step.tool == "filesystem.write_file"
    assert write_step.requires_approval is True
    assert write_step.arguments["path"] == "debug_demo.py"
    assert write_step.arguments["content"] == "print('fixed')\n"

    assert read_step.tool == "filesystem.read_file"
    assert read_step.depends_on == [write_step.step_id]

    # Planning must never change the file.
    assert (tmp_path / "debug_demo.py").read_text(
        encoding="utf-8"
    ) == "print('original')\n"


def test_read_only_request_still_uses_read_only_planner(tmp_path):
    (tmp_path / "README.md").write_text("PHANES\n", encoding="utf-8")

    planner = EditAwarePlanner(
        edit_planner=EditPlanner(FakeProviderManager())
    )
    plan = planner("Read the README.md file", str(tmp_path), {})

    assert plan.steps[0].tool == "filesystem.read_file"
    assert plan.steps[0].arguments == {"path": "README.md"}
