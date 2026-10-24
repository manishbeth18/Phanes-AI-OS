
"""Adapter from a selected workspace path to PHANES ContextEngine."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from context.context_engine import ContextEngine


class _WorkspaceRepository:
    def get(self, workspace_id: str) -> Any | None:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            return None

        try:
            root = Path(workspace_id).expanduser().resolve(strict=True)
            if not root.is_dir():
                return None
        except (OSError, RuntimeError, ValueError):
            return None

        return SimpleNamespace(
            workspace_id=workspace_id,
            root_path=str(root),
        )


class _ContextStorage:
    def __init__(self) -> None:
        self.workspaces = _WorkspaceRepository()


class WorkspaceContextProvider:
    """Callable context provider compatible with ConcreteTaskService."""

    def __init__(self) -> None:
        self._engine = ContextEngine(_ContextStorage())

    def __call__(self, workspace: str, goal: str):
        return self._engine.get_relevant_context(
            str(Path(workspace).expanduser().resolve(strict=True)),
            goal,
        )
