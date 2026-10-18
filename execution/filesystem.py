
"""Workspace-restricted, read-only filesystem tools for PHANES."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from execution.tool_registry import ToolRegistry, ToolSafetyLevel


class FilesystemToolError(Exception):
    """Raised when a filesystem operation is unsafe or invalid."""


class FilesystemTools:
    """Provide read-only filesystem operations inside one workspace."""

    def __init__(self, workspace_root: str | Path) -> None:
        root = Path(workspace_root).expanduser().resolve(strict=True)

        if not root.is_dir():
            raise ValueError("Workspace root must be a directory.")

        self.workspace_root = root

    def _resolve_path(self, path: str) -> Path:
        """Resolve a relative path and reject workspace escapes."""
        if not isinstance(path, str) or not path.strip():
            raise FilesystemToolError("Path must be a non-empty string.")

        requested = Path(path)

        if requested.is_absolute():
            raise FilesystemToolError(
                "Absolute paths are not allowed. Use a workspace-relative path."
            )

        try:
            resolved = (self.workspace_root / requested).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise FilesystemToolError(
                "Path does not exist or cannot be resolved."
            ) from exc

        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise FilesystemToolError(
                "Access outside the workspace is not allowed."
            ) from None

        return resolved

    def list_directory(self, path: str = ".") -> list[dict[str, Any]]:
        """List entries in a workspace directory without reading file contents."""
        target = self._resolve_path(path)

        if not target.is_dir():
            raise FilesystemToolError("The requested path is not a directory.")

        entries: list[dict[str, Any]] = []

        try:
            for item in sorted(
                target.iterdir(),
                key=lambda entry: entry.name.lower(),
            ):
                entries.append(
                    {
                        "name": item.name,
                        "type": "directory" if item.is_dir() else "file",
                    }
                )
        except OSError as exc:
            raise FilesystemToolError(
                "The directory could not be listed."
            ) from exc

        return entries

    def read_file(
        self,
        path: str,
        max_bytes: int = 100_000,
    ) -> str:
        """Read a bounded amount of UTF-8 text from a workspace file."""
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int):
            raise FilesystemToolError("max_bytes must be an integer.")

        if not 1 <= max_bytes <= 1_000_000:
            raise FilesystemToolError(
                "max_bytes must be between 1 and 1,000,000."
            )

        target = self._resolve_path(path)

        if not target.is_file():
            raise FilesystemToolError("The requested path is not a file.")

        try:
            with target.open("rb") as file:
                content = file.read(max_bytes + 1)
        except OSError as exc:
            raise FilesystemToolError("The file could not be read.") from exc

        if len(content) > max_bytes:
            raise FilesystemToolError(
                f"File exceeds the {max_bytes}-byte read limit."
            )

        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FilesystemToolError(
                "The file is not valid UTF-8 text."
            ) from exc

    def file_info(self, path: str) -> dict[str, Any]:
        """Return basic metadata for a workspace file or directory."""
        target = self._resolve_path(path)

        try:
            stat = target.stat()
        except OSError as exc:
            raise FilesystemToolError(
                "File metadata could not be retrieved."
            ) from exc

        return {
            "name": target.name or str(target),
            "type": "directory" if target.is_dir() else "file",
            "size_bytes": stat.st_size,
            "modified_time": stat.st_mtime,
        }


def register_filesystem_tools(
    registry: ToolRegistry,
    workspace_root: str | Path,
) -> FilesystemTools:
    """Register filesystem operations and return their bound tool provider."""
    filesystem = FilesystemTools(workspace_root)

    registry.register(
        "filesystem.list_directory",
        filesystem.list_directory,
        safety_level=ToolSafetyLevel.READ_ONLY,
    )
    registry.register(
        "filesystem.read_file",
        filesystem.read_file,
        safety_level=ToolSafetyLevel.READ_ONLY,
    )
    registry.register(
        "filesystem.file_info",
        filesystem.file_info,
        safety_level=ToolSafetyLevel.READ_ONLY,
    )

    return filesystem
