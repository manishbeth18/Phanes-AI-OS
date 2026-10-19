"""Controlled terminal execution for PHANES."""

from __future__ import annotations

import codecs
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, TextIO

from execution.tool_registry import ToolRegistry, ToolSafetyLevel


class TerminalToolError(Exception):
    """Raised when a terminal operation is invalid or unsafe."""


class TerminalTools:
    """Run bounded terminal commands inside a configured workspace."""

    def __init__(self, workspace_root: str | Path) -> None:
        root = Path(workspace_root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Workspace root must be a directory.")

        self.workspace_root = root
        self._process_lock = threading.Lock()
        self._active_processes: dict[int, subprocess.Popen[bytes]] = {}
        self._cancelled_pids: set[int] = set()

    def _resolve_cwd(self, cwd: str = ".") -> Path:
        """Resolve a working directory and reject workspace escapes."""
        if not isinstance(cwd, str) or not cwd.strip():
            raise TerminalToolError(
                "Working directory must be a non-empty string."
            )

        requested = Path(cwd)
        if requested.is_absolute():
            raise TerminalToolError(
                "Absolute working directories are not allowed."
            )

        try:
            resolved = (self.workspace_root / requested).resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise TerminalToolError(
                "Working directory does not exist or cannot be resolved."
            ) from exc

        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise TerminalToolError(
                "Working directory must remain inside the workspace."
            ) from None

        if not resolved.is_dir():
            raise TerminalToolError(
                "Working directory must be a directory."
            )
        return resolved

    @staticmethod
    def _validate_cancel_event(
        cancel_event: threading.Event | None,
    ) -> None:
        if cancel_event is not None and not isinstance(
            cancel_event, threading.Event
        ):
            raise TerminalToolError(
                "cancel_event must be a threading.Event or None."
            )

    @staticmethod
    def _drain_stream(
        stream: Any,
        max_chars: int,
        state: dict[str, Any],
    ) -> None:
        """Drain a pipe continuously while retaining only bounded output."""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        retained: list[str] = []
        retained_length = 0
        truncated = False

        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if not text:
                    continue

                remaining = max_chars - retained_length
                if remaining > 0:
                    kept = text[:remaining]
                    if kept:
                        retained.append(kept)
                        retained_length += len(kept)
                if len(text) > max(remaining, 0):
                    truncated = True

            tail = decoder.decode(b"", final=True)
            if tail:
                remaining = max_chars - retained_length
                if remaining > 0:
                    retained.append(tail[:remaining])
                    retained_length += min(len(tail), remaining)
                if len(tail) > max(remaining, 0):
                    truncated = True
        except (OSError, ValueError):
            # Stream closure during forced process termination is expected.
            pass
        finally:
            state["text"] = "".join(retained)
            state["truncated"] = truncated
            try:
                stream.close()
            except OSError:
                pass

    @staticmethod
    def _format_output(state: dict[str, Any], max_chars: int) -> str:
        """Add a truncation marker without exceeding the configured limit."""
        output = str(state.get("text", ""))
        if not state.get("truncated", False):
            return output[:max_chars]

        suffix = "\n... [output truncated]"
        if max_chars <= len(suffix):
            return suffix[:max_chars]
        return output[: max_chars - len(suffix)] + suffix

    @staticmethod
    def _terminate_process(
        process: subprocess.Popen[bytes],
        grace_seconds: float = 1.0,
    ) -> None:
        """Terminate a command and attempt to clean up its process tree."""
        if os.name == "nt":
            # Try a graceful process-group signal first when the parent lives.
            if process.poll() is None:
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                except (OSError, ProcessLookupError, ValueError):
                    pass

            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                pass

            # Attempt tree cleanup even if the parent exited during the grace
            # period; descendants may still hold inherited pipe handles.
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=max(grace_seconds, 0.1),
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            # A process group can outlive its original parent.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                if process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass

            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                pass

            # The original parent may exit while a descendant ignores SIGTERM.
            # Escalate against the group regardless of the parent's exit state.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass

        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=grace_seconds)
            except subprocess.TimeoutExpired:
                pass

    def run_command(
        self,
        command: list[str],
        cwd: str = ".",
        timeout_seconds: int = 10,
        max_output_chars: int = 10_000,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Execute an argument-list command without invoking a shell.

        The caller must obtain approval through PHANES's executor before
        invoking this tool. This method itself does not grant approval.
        """
        if not isinstance(command, list) or not command:
            raise TerminalToolError(
                "Command must be a non-empty list of strings."
            )
        if any(not isinstance(part, str) for part in command):
            raise TerminalToolError(
                "Every command argument must be a string."
            )
        if any("\x00" in part for part in command):
            raise TerminalToolError(
                "Command arguments cannot contain NUL characters."
            )
        if not command[0].strip():
            raise TerminalToolError("Command executable cannot be empty.")

        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 60
        ):
            raise TerminalToolError(
                "timeout_seconds must be an integer between 1 and 60."
            )
        if (
            isinstance(max_output_chars, bool)
            or not isinstance(max_output_chars, int)
            or not 1 <= max_output_chars <= 100_000
        ):
            raise TerminalToolError(
                "max_output_chars must be an integer between 1 and 100000."
            )

        self._validate_cancel_event(cancel_event)
        working_directory = self._resolve_cwd(cwd)

        # Do not pass common API credentials to child processes.
        child_env = os.environ.copy()
        sensitive_fragments = (
            "API_KEY",
            "API_TOKEN",
            "SECRET",
            "PASSWORD",
            "ACCESS_TOKEN",
            "AUTH_TOKEN",
            "PRIVATE_KEY",
        )
        for key in list(child_env):
            upper_key = key.upper()
            if any(fragment in upper_key for fragment in sensitive_fragments):
                child_env.pop(key, None)

        creation_kwargs: dict[str, Any] = {}
        if os.name == "nt":
            creation_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            creation_kwargs["start_new_session"] = True

        try:
            process = subprocess.Popen(
                command,
                cwd=str(working_directory),
                shell=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
                env=child_env,
                **creation_kwargs,
            )
        except OSError as exc:
            return {
                "success": False,
                "return_code": None,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
                "cancelled": False,
                "error": f"Unable to start command: {exc}",
            }

        pid = process.pid
        with self._process_lock:
            self._active_processes[pid] = process
            self._cancelled_pids.discard(pid)

        stdout_state: dict[str, Any] = {"text": "", "truncated": False}
        stderr_state: dict[str, Any] = {"text": "", "truncated": False}
        assert process.stdout is not None
        assert process.stderr is not None

        stdout_thread = threading.Thread(
            target=self._drain_stream,
            args=(process.stdout, max_output_chars, stdout_state),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._drain_stream,
            args=(process.stderr, max_output_chars, stderr_state),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        deadline = time.monotonic() + timeout_seconds
        timed_out = False
        cancelled = False

        try:
            while process.poll() is None:
                with self._process_lock:
                    externally_cancelled = pid in self._cancelled_pids
                event_cancelled = (
                    cancel_event is not None and cancel_event.is_set()
                )

                if externally_cancelled or event_cancelled:
                    cancelled = True
                    self._terminate_process(process)
                    break

                if time.monotonic() >= deadline:
                    timed_out = True
                    self._terminate_process(process)
                    break

                time.sleep(0.025)

            # The readers drain concurrently, so ordinary high-volume output
            # cannot accumulate unboundedly in the parent process's memory.
            stdout_thread.join(timeout=2.0)
            stderr_thread.join(timeout=2.0)

            # If descendants keep inherited pipes open, force-close our pipe
            # handles so this call cannot wait indefinitely for reader threads.
            for thread, stream in (
                (stdout_thread, process.stdout),
                (stderr_thread, process.stderr),
            ):
                if thread.is_alive():
                    try:
                        stream.close()
                    except OSError:
                        pass
                    thread.join(timeout=0.25)

            stdout = self._format_output(stdout_state, max_output_chars)
            stderr = self._format_output(stderr_state, max_output_chars)

            if timed_out:
                return {
                    "success": False,
                    "return_code": None,
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": True,
                    "cancelled": False,
                    "error": f"Command timed out after {timeout_seconds} seconds.",
                }
            if cancelled:
                return {
                    "success": False,
                    "return_code": None,
                    "stdout": stdout,
                    "stderr": stderr,
                    "timed_out": False,
                    "cancelled": True,
                    "error": "Command was cancelled.",
                }

            return {
                "success": process.returncode == 0,
                "return_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "timed_out": False,
                "cancelled": False,
                "error": (
                    None
                    if process.returncode == 0
                    else "Command exited with a non-zero return code."
                ),
            }
        finally:
            with self._process_lock:
                self._active_processes.pop(pid, None)
                self._cancelled_pids.discard(pid)

    def cancel_active(self) -> list[int]:
        """Cancel currently running commands and return their PIDs."""
        with self._process_lock:
            active = list(self._active_processes.items())
            self._cancelled_pids.update(pid for pid, _ in active)

        for _, process in active:
            self._terminate_process(process)

        return [pid for pid, _ in active]


def register_terminal_tools(
    registry: ToolRegistry,
    workspace_root: str | Path,
) -> TerminalTools:
    """Register terminal tools with mandatory approval."""
    terminal = TerminalTools(workspace_root)
    registry.register(
        "terminal.run_command",
        terminal.run_command,
        safety_level=ToolSafetyLevel.APPROVAL_REQUIRED,
    )
    return terminal
