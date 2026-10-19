
"""Workspace-confined filesystem tools for PHANES."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from execution.tool_registry import ToolRegistry, ToolSafetyLevel


class FilesystemToolError(Exception):
    """Raised when a filesystem operation is unsafe or invalid."""


class FilesystemTools:
    """Provide workspace-confined read and file mutation operations."""

    def __init__(self, workspace_root: str | Path) -> None:
        root = Path(workspace_root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Workspace root must be a directory.")
        self.workspace_root = root

    def _resolve_path(
        self,
        path: str,
        *,
        must_exist: bool = True,
    ) -> Path:
        """Resolve a workspace-relative path and reject workspace escapes."""
        if not isinstance(path, str) or not path.strip():
            raise FilesystemToolError("Path must be a non-empty string.")

        requested = Path(path)
        if requested.is_absolute():
            raise FilesystemToolError(
                "Absolute paths are not allowed. Use a workspace-relative path."
            )

        try:
            resolved = (
                self.workspace_root / requested
            ).resolve(strict=must_exist)
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

        if not must_exist:
            # The parent must exist and resolve within the workspace.
            # This also prevents creating a file through an escaping symlink.
            try:
                resolved.parent.resolve(strict=True).relative_to(
                    self.workspace_root
                )
            except (OSError, RuntimeError, ValueError):
                raise FilesystemToolError(
                    "The parent directory must exist inside the workspace."
                ) from None

        return resolved

    def list_directory(self, path: str = ".") -> list[dict[str, Any]]:
        """List entries in a workspace directory."""
        target = self._resolve_path(path)
        if not target.is_dir():
            raise FilesystemToolError("The requested path is not a directory.")

        try:
            return [
                {
                    "name": item.name,
                    "type": "directory" if item.is_dir() else "file",
                }
                for item in sorted(
                    target.iterdir(),
                    key=lambda entry: entry.name.lower(),
                )
            ]
        except OSError as exc:
            raise FilesystemToolError(
                "The directory could not be listed."
            ) from exc

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

    def create_file(self, path: str, content: str = "") -> dict[str, Any]:
        """Create a new UTF-8 file without overwriting an existing file."""
        if not isinstance(content, str):
            raise FilesystemToolError("File content must be a string.")

        target = self._resolve_path(path, must_exist=False)
        if target == self.workspace_root:
            raise FilesystemToolError("A file path is required.")
        if target.exists():
            raise FilesystemToolError(
                "The path already exists; use write_file to replace file content."
            )
        if not target.parent.is_dir():
            raise FilesystemToolError(
                "The parent directory must already exist."
            )

        try:
            with target.open("x", encoding="utf-8", newline="") as file:
                file.write(content)
        except FileExistsError as exc:
            raise FilesystemToolError("The path already exists.") from exc
        except OSError as exc:
            raise FilesystemToolError("The file could not be created.") from exc

        return {
            "path": target.relative_to(self.workspace_root).as_posix(),
            "created": True,
            "bytes_written": len(content.encode("utf-8")),
        }

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        """Write UTF-8 text, creating a file or replacing existing file content."""
        if not isinstance(content, str):
            raise FilesystemToolError("File content must be a string.")

        target = self._resolve_path(path, must_exist=False)
        if target == self.workspace_root:
            raise FilesystemToolError("A file path is required.")
        if target.exists() and not target.is_file():
            raise FilesystemToolError("The requested path is not a file.")
        if not target.parent.is_dir():
            raise FilesystemToolError(
                "The parent directory must already exist."
            )

        try:
            with target.open("w", encoding="utf-8", newline="") as file:
                file.write(content)
        except OSError as exc:
            raise FilesystemToolError("The file could not be written.") from exc

        return {
            "path": target.relative_to(self.workspace_root).as_posix(),
            "written": True,
            "bytes_written": len(content.encode("utf-8")),
        }

    def delete_file(self, path: str) -> dict[str, Any]:
        """Delete a file inside the workspace; directories are not deleted."""
        target = self._resolve_path(path)
        if target == self.workspace_root:
            raise FilesystemToolError("The workspace root cannot be deleted.")
        if not target.is_file():
            raise FilesystemToolError("The requested path is not a file.")

        relative_path = target.relative_to(self.workspace_root).as_posix()
        try:
            target.unlink()
        except OSError as exc:
            raise FilesystemToolError("The file could not be deleted.") from exc

        return {"path": relative_path, "deleted": True}


def register_filesystem_tools(
    registry: ToolRegistry,
    workspace_root: str | Path,
) -> FilesystemTools:
    """Register filesystem tools and return their provider."""
    filesystem = FilesystemTools(workspace_root)

    for name, tool in (
        ("filesystem.list_directory", filesystem.list_directory),
        ("filesystem.read_file", filesystem.read_file),
        ("filesystem.file_info", filesystem.file_info),
    ):
        registry.register(
            name,
            tool,
            safety_level=ToolSafetyLevel.READ_ONLY,
        )

    for name, tool in (
        ("filesystem.create_file", filesystem.create_file),
        ("filesystem.write_file", filesystem.write_file),
        ("filesystem.delete_file", filesystem.delete_file),
    ):
        registry.register(
            name,
            tool,
            safety_level=ToolSafetyLevel.APPROVAL_REQUIRED,
        )

    return filesystem
