
"""Register all currently implemented PHANES execution tools."""

from __future__ import annotations

from pathlib import Path

from execution.browser import register_browser_tools
from execution.executor import Executor
from execution.filesystem import register_filesystem_tools
from execution.git_tools import register_git_tools
from execution.terminal import register_terminal_tools
from execution.tool_registry import ToolRegistry


def create_execution_engine(
    workspace_root: str | Path,
    repository_root: str | Path | None = None,
) -> tuple[ToolRegistry, Executor]:
    """Create the registry, register tools, and return the executor.

    Args:
        workspace_root: Root directory accessible to filesystem and terminal tools.
        repository_root: Git repository root. Defaults to workspace_root.

    Returns:
        A tuple containing the populated registry and its executor.
    """
    workspace = Path(workspace_root).expanduser().resolve(strict=True)
    repository = (
        Path(repository_root).expanduser().resolve(strict=True)
        if repository_root is not None
        else workspace
    )

    if not workspace.is_dir():
        raise ValueError("workspace_root must be a directory.")

    if not repository.is_dir():
        raise ValueError("repository_root must be a directory.")

    registry = ToolRegistry()

    register_filesystem_tools(registry, workspace)
    register_terminal_tools(registry, workspace)
    register_git_tools(registry, repository)
    register_browser_tools(registry)

    executor = Executor(registry)

    return registry, executor
