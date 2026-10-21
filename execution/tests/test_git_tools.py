
import subprocess

import pytest

from execution.executor import Executor
from execution.git_tools import GitToolError, GitTools, register_git_tools
from execution.tool_registry import ToolRegistry


@pytest.fixture
def git_repo(tmp_path):
    def run_git(*args):
        subprocess.run(
            ["git", *args],
            cwd=tmp_path,
            check=True,
            capture_output=True,
            text=True,
        )

    run_git("init")
    run_git("config", "user.name", "PHANES Tests")
    run_git("config", "user.email", "phanes-tests@example.com")

    (tmp_path / "README.md").write_text(
        "# PHANES test repository\n",
        encoding="utf-8",
    )

    run_git("add", "README.md")
    run_git("commit", "-m", "Initial test commit")

    return tmp_path


@pytest.fixture
def git_tools(git_repo):
    return GitTools(git_repo)


def test_status_shows_current_branch(git_tools):
    result = git_tools.status()

    assert "## " in result["output"]


def test_status_shows_untracked_file(git_tools, git_repo):
    (git_repo / "new_file.txt").write_text("hello", encoding="utf-8")

    result = git_tools.status()

    assert "new_file.txt" in result["output"]


def test_current_branch_returns_branch_name(git_tools):
    result = git_tools.current_branch()

    assert result["branch"]


def test_diff_shows_unstaged_changes(git_tools, git_repo):
    (git_repo / "README.md").write_text(
        "# PHANES updated\n",
        encoding="utf-8",
    )

    result = git_tools.diff()

    assert "PHANES updated" in result["diff"]


def test_diff_shows_staged_changes(git_tools, git_repo):
    (git_repo / "README.md").write_text(
        "# PHANES staged update\n",
        encoding="utf-8",
    )

    subprocess.run(
        ["git", "add", "README.md"],
        cwd=git_repo,
        check=True,
    )

    result = git_tools.diff(staged=True)

    assert "PHANES staged update" in result["diff"]


def test_recent_commits_returns_commit(git_tools):
    result = git_tools.recent_commits()

    assert "Initial test commit" in result["commits"]


def test_recent_commits_rejects_invalid_limit(git_tools):
    with pytest.raises(GitToolError):
        git_tools.recent_commits(limit=0)


def test_non_repository_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        GitTools(tmp_path)


def test_git_tools_register_and_execute(git_repo):
    registry = ToolRegistry()
    register_git_tools(registry, git_repo)
    executor = Executor(registry)

    result = executor.execute("git.current_branch")

    assert result.success is True
    assert result.output["branch"]


def test_diff_rejects_non_boolean_staged(git_tools):
    with pytest.raises(GitToolError):
        git_tools.diff(staged="yes")


def test_recent_commits_rejects_boolean_limit(git_tools):
    with pytest.raises(GitToolError):
        git_tools.recent_commits(limit=True)
