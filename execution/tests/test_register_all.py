
import subprocess

from execution.register_all import create_execution_engine


def initialize_git_repo(path):
    """Initialize a temporary Git repository for integration tests."""
    subprocess.run(
        ["git", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "PHANES Tests"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "phanes-tests@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )

    (path / "README.md").write_text(
        "# PHANES integration test\n",
        encoding="utf-8",
    )

    subprocess.run(
        ["git", "add", "README.md"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial test commit"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def test_create_execution_engine_registers_all_tools(tmp_path):
    initialize_git_repo(tmp_path)

    registry, executor = create_execution_engine(tmp_path)

    expected_tools = {
        "filesystem.list_directory",
        "filesystem.read_file",
        "filesystem.file_info",
        "filesystem.create_file",
        "filesystem.write_file",
        "filesystem.delete_file",
        "terminal.run_command",
        "git.status",
        "git.current_branch",
        "git.diff",
        "git.recent_commits",
        "browser.fetch_page",
        "browser.open_page",
        "browser.inspect_page",
        "browser.close_page",
        "browser.click",
        "browser.fill",
    }

    assert set(registry.list_tools()) == expected_tools
    assert len(registry) == len(expected_tools)
    assert executor.registry is registry


def test_filesystem_tool_runs_through_integrated_executor(tmp_path):
    initialize_git_repo(tmp_path)

    (tmp_path / "hello.txt").write_text(
        "Hello from PHANES",
        encoding="utf-8",
    )

    _, executor = create_execution_engine(tmp_path)

    result = executor.execute(
        "filesystem.read_file",
        {"path": "hello.txt"},
    )

    assert result.success is True
    assert result.output == "Hello from PHANES"



def test_terminal_tool_runs_through_integrated_executor(tmp_path):
    import sys

    initialize_git_repo(tmp_path)
    _, executor = create_execution_engine(tmp_path)

    arguments = {
        "command": [
            sys.executable,
            "-c",
            "print('PHANES integration works')",
        ]
    }
    token = executor.approval_manager.issue(
        "terminal.run_command",
        arguments,
    )

    result = executor.execute(
        "terminal.run_command",
        arguments,
        approval_token=token,
    )

    assert result.success is True
    assert "PHANES integration works" in str(result.output)


def test_repository_root_can_be_configured_separately(tmp_path):
    workspace = tmp_path / "workspace"
    repository = tmp_path / "repository"

    workspace.mkdir()
    repository.mkdir()
    initialize_git_repo(repository)

    registry, _ = create_execution_engine(
        workspace_root=workspace,
        repository_root=repository,
    )

    assert "git.status" in registry
