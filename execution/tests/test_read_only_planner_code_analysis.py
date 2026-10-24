
import pytest

from shared.read_only_planner import (
    ReadOnlyPlanner,
    UnsupportedRequestError,
)


@pytest.fixture
def planner():
    return ReadOnlyPlanner()


def test_plans_read_of_python_file_for_debugging(planner):
    plan = planner(
        "Read the contents of debug_demo.py and identify the bug.",
        ".",
        None,
    )

    assert len(plan.steps) == 1
    assert plan.steps[0].tool == "filesystem.read_file"
    assert plan.steps[0].arguments["path"] == "debug_demo.py"


def test_plans_nested_file_for_code_review(planner):
    plan = planner(
        "Analyze src/main.py for bugs without modifying it.",
        ".",
        None,
    )

    assert plan.steps[0].tool == "filesystem.read_file"
    assert plan.steps[0].arguments["path"] == "src/main.py"


def test_rejects_parent_directory_traversal(planner):
    with pytest.raises(UnsupportedRequestError):
        planner(
            "Read the file ../outside.py and identify the bug.",
            ".",
            None,
        )


def test_rejects_debug_request_without_filename(planner):
    with pytest.raises(UnsupportedRequestError):
        planner(
            "Debug the code and find the bug.",
            ".",
            None,
        )


def test_existing_readme_support_still_works(planner):
    plan = planner(
        "Read the contents of README.md in this workspace.",
        ".",
        None,
    )

    assert plan.steps[0].tool == "filesystem.read_file"
    assert plan.steps[0].arguments["path"] == "README.md"

