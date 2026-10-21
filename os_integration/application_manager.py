import os
import subprocess
from pathlib import Path


class ApplicationManager:
    """Launch explicitly approved applications safely."""

    def __init__(self, allowed_applications: dict[str, str] | None = None):
        self.allowed_applications = dict(allowed_applications or {})

    def launch(
        self,
        application: str,
        workspace: str | Path,
        arguments: list[str] | None = None,
    ) -> int:
        """Launch an approved application in an existing workspace."""

        if os.name != "nt":
            raise OSError("ApplicationManager requires Windows.")

        if not isinstance(application, str) or not application.strip():
            raise ValueError("Application name must be a non-empty string.")

        executable = self.allowed_applications.get(application)

        if not executable:
            raise ValueError(
                f"Application '{application}' is not in the allowlist."
            )

        if not isinstance(executable, str) or "\0" in executable:
            raise ValueError("Configured executable path is invalid.")

        executable_path = Path(executable).expanduser().resolve()

        if not executable_path.is_file():
            raise FileNotFoundError(
                f"Application executable not found: {executable_path}"
            )

        workspace_path = Path(workspace).expanduser().resolve()

        if not workspace_path.is_dir():
            raise ValueError("Workspace must be an existing directory.")

        if arguments is None:
            arguments = []

        if not isinstance(arguments, list):
            raise TypeError("arguments must be a list of strings.")

        if not all(
            isinstance(arg, str) and "\0" not in arg
            for arg in arguments
        ):
            raise ValueError("Every argument must be a valid string.")

        process = subprocess.Popen(
            [str(executable_path), *arguments],
            cwd=str(workspace_path),
            shell=False,
        )

        return process.pid