from pathlib import Path


class WorkspaceDetector:
    """Detect and validate project workspace directories."""

    PROJECT_MARKERS = {
        ".git",
        "pyproject.toml",
        "requirements.txt",
        "package.json",
        "Cargo.toml",
        "go.mod",
        ".sln",
    }

    def detect_workspace(self, path: str | Path) -> Path | None:
        """Find the nearest project root from a file or directory path."""

        candidate = Path(path).expanduser().resolve()

        if not candidate.exists():
            return None

        if candidate.is_file():
            candidate = candidate.parent

        for directory in (candidate, *candidate.parents):
            if any(
                (directory / marker).exists()
                for marker in self.PROJECT_MARKERS
            ):
                return directory

        return None

    def validate_workspace(self, path: str | Path) -> bool:
        """Check whether the given path exists and is a directory."""

        try:
            candidate = Path(path).expanduser().resolve()
            return candidate.exists() and candidate.is_dir()
        except (OSError, RuntimeError, TypeError, ValueError):
            return False