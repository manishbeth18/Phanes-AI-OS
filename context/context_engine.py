"""Workspace context retrieval for the PHANES planner.

This module stays independent of the planner and UI. It reads workspace metadata
through the storage repository and performs bounded, read-only local file discovery.
Optional task-history repository APIs are used only when present; absence is
represented as an empty history rather than guessed data.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable
import re

from shared.context import ContextFile, ConversationMessage, PreviousTaskResult, TaskContext

IGNORED_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", "target", ".idea", ".vscode",
}
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".toml", ".ini", ".cfg", ".json", ".yaml", ".yml",
    ".html", ".css", ".js", ".ts", ".tsx", ".jsx", ".sql", ".bat", ".ps1", ".sh",
    ".env.example", ".gitignore",
}
MAX_FILES = 12
MAX_FILE_BYTES = 80_000
MAX_TOTAL_CHARS = 60_000
MAX_PREVIOUS_RESULTS = 5


class ContextEngine:
    """Build a bounded, read-only TaskContext from a known workspace."""

    def __init__(self, storage: Any, *, max_files: int = MAX_FILES,
                 max_file_bytes: int = MAX_FILE_BYTES, max_total_chars: int = MAX_TOTAL_CHARS):
        self.storage = storage
        self.max_files = max(0, int(max_files))
        self.max_file_bytes = max(1, int(max_file_bytes))
        self.max_total_chars = max(0, int(max_total_chars))

    def get_relevant_context(self, workspace_id: str, goal: str) -> TaskContext:
        workspace = self.storage.workspaces.get(workspace_id)
        if workspace is None:
            raise ValueError(f"Workspace not found: {workspace_id}")

        root = Path(workspace.root_path).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Workspace path is not an accessible directory: {root}")

        relevant_files = self._collect_files(root, goal)
        previous_results = self._collect_previous_results(workspace_id, goal)
        project_type = self._detect_project_type(root)
        return TaskContext(
            workspace_id=str(getattr(workspace, "workspace_id", workspace_id)),
            workspace_path=str(root),
            project_type=project_type,
            relevant_files=relevant_files,
            previous_results=previous_results,
            conversation_history=[ConversationMessage(role="user", content=goal.strip())] if goal.strip() else [],
            metadata={
                "context_version": 1,
                "files_included": len(relevant_files),
                "previous_results_included": len(previous_results),
                "file_content_is_read_only": True,
            },
        )

    def _collect_files(self, root: Path, goal: str) -> list[ContextFile]:
        if self.max_files == 0 or self.max_total_chars == 0:
            return []
        keywords = {word.lower() for word in re.findall(r"[A-Za-z0-9_+-]{3,}", goal)}
        candidates: list[tuple[int, str, Path]] = []
        try:
            for path in root.rglob("*"):
                if not path.is_file() or self._is_ignored(path, root):
                    continue
                if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"Dockerfile", "Makefile"}:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if size > self.max_file_bytes:
                    continue
                rel = path.relative_to(root).as_posix()
                searchable = (rel + " " + path.stem).lower()
                score = sum(3 for word in keywords if word in searchable)
                if path.name.lower() in {"readme.md", "pyproject.toml", "requirements.txt", "package.json", "main.py"}:
                    score += 2
                candidates.append((-score, rel.lower(), path))
        except OSError:
            return []

        candidates.sort()
        output: list[ContextFile] = []
        used_chars = 0
        for _neg_score, rel, path in candidates:
            if len(output) >= self.max_files or used_chars >= self.max_total_chars:
                break
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeError):
                continue
            remaining = self.max_total_chars - used_chars
            if len(content) > remaining:
                content = content[:remaining]
            output.append(ContextFile(path=rel, content=content, description="Workspace text file selected by bounded relevance scan"))
            used_chars += len(content)
        return output

    @staticmethod
    def _is_ignored(path: Path, root: Path) -> bool:
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            return True
        return any(part in IGNORED_DIRS for part in parts) or any(part.startswith(".") and part not in {".env.example"} for part in parts[:-1])

    @staticmethod
    def _detect_project_type(root: Path) -> str | None:
        if (root / "pyproject.toml").exists() or (root / "requirements.txt").exists() or any(root.glob("*.py")):
            return "python"
        if (root / "package.json").exists():
            return "javascript/typescript"
        if (root / "Cargo.toml").exists():
            return "rust"
        if (root / "go.mod").exists():
            return "go"
        if (root / "pom.xml").exists() or (root / "build.gradle").exists():
            return "java"
        return None

    def _collect_previous_results(self, workspace_id: str, goal: str) -> list[PreviousTaskResult]:
        """Use a documented history API if the storage implementation exposes one.

        Storage APIs are still being coordinated. Supported optional names are
        list_for_workspace(workspace_id) on storage.tasks or
        list_by_workspace(workspace_id) on storage.tasks. No direct SQL access.
        """
        tasks_repo = getattr(self.storage, "tasks", None)
        if tasks_repo is None:
            return []
        records = None
        for method_name in ("list_for_workspace", "list_by_workspace"):
            method = getattr(tasks_repo, method_name, None)
            if callable(method):
                try:
                    records = method(workspace_id)
                except (TypeError, NotImplementedError):
                    records = None
                if records is not None:
                    break
        if records is None:
            return []
        results: list[PreviousTaskResult] = []
        goal_terms = {w.lower() for w in re.findall(r"[A-Za-z0-9_+-]{3,}", goal)}
        for record in records:
            task_id = str(getattr(record, "task_id", ""))
            summary = str(getattr(record, "summary", "") or getattr(record, "goal", "") or "")
            status_obj = getattr(record, "status", None)
            status = str(getattr(status_obj, "value", status_obj)) if status_obj is not None else None
            if not task_id or not summary:
                continue
            overlap = len(goal_terms & {w.lower() for w in re.findall(r"[A-Za-z0-9_+-]{3,}", summary)})
            results.append((overlap, PreviousTaskResult(
                task_id=task_id, summary=summary, status=status,
                output=getattr(record, "output", None),
                verified=bool(getattr(record, "verified", False)),
                verification_details=getattr(record, "verification_details", None),
            )))
        results.sort(key=lambda item: item[0], reverse=True)
        return [result for _score, result in results[:MAX_PREVIOUS_RESULTS]]
