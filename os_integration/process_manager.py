import ctypes
import os
import subprocess
from ctypes import wintypes
from pathlib import Path


class ProcessManager:
    """Manage processes and discover Windows processes and windows."""

    def __init__(self):
        self._owned_processes: dict[int, subprocess.Popen] = {}

    def start_process(
        self,
        command: list[str],
        cwd: str | Path,
    ) -> int:
        """Start a process without using the shell."""
        if os.name != "nt":
            raise OSError("ProcessManager requires Windows.")

        if not isinstance(command, list) or not command:
            raise ValueError("command must be a non-empty list of strings")

        if not all(isinstance(arg, str) and "\0" not in arg for arg in command):
            raise ValueError("Every command argument must be a valid string")

        working_directory = Path(cwd).expanduser().resolve()

        if not working_directory.is_dir():
            raise ValueError("Working directory does not exist")

        process = subprocess.Popen(
            command,
            cwd=str(working_directory),
            shell=False,
        )

        self._owned_processes[process.pid] = process
        return process.pid

    def list_processes(self) -> list[dict]:
        """Return running Windows processes using the Windows API."""
        if os.name != "nt":
            raise OSError("Process discovery requires Windows.")

        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [
            wintypes.DWORD, wintypes.DWORD
        ]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

        kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)
        ]
        kernel32.Process32FirstW.restype = wintypes.BOOL

        kernel32.Process32NextW.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)
        ]
        kernel32.Process32NextW.restype = wintypes.BOOL

        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        snapshot = kernel32.CreateToolhelp32Snapshot(
            TH32CS_SNAPPROCESS, 0
        )

        if snapshot == INVALID_HANDLE_VALUE:
            raise ctypes.WinError(ctypes.get_last_error())

        results = []
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)

        try:
            success = kernel32.Process32FirstW(
                snapshot, ctypes.byref(entry)
            )

            while success:
                results.append({
                    "pid": entry.th32ProcessID,
                    "parent_pid": entry.th32ParentProcessID,
                    "name": entry.szExeFile,
                })

                success = kernel32.Process32NextW(
                    snapshot, ctypes.byref(entry)
                )
        finally:
            kernel32.CloseHandle(snapshot)

        return results

    def list_windows(self) -> list[dict]:
        """Return visible top-level Windows windows."""
        if os.name != "nt":
            raise OSError("Window discovery requires Windows.")

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )

        user32.EnumWindows.argtypes = [
            EnumWindowsProc, wintypes.LPARAM
        ]
        user32.EnumWindows.restype = wintypes.BOOL

        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL

        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int

        user32.GetWindowTextW.argtypes = [
            wintypes.HWND, wintypes.LPWSTR, ctypes.c_int
        ]
        user32.GetWindowTextW.restype = ctypes.c_int

        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        results = []

        @EnumWindowsProc
        def callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True

            length = user32.GetWindowTextLengthW(hwnd)

            if length <= 0:
                return True

            title_buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title_buffer, length + 1)

            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

            results.append({
                "hwnd": int(hwnd),
                "pid": pid.value,
                "title": title_buffer.value,
            })
            return True

        if not user32.EnumWindows(callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())

        return results

    def terminate_process(self, pid: int) -> bool:
        """Terminate only a process started and tracked by this manager."""
        process = self._owned_processes.get(pid)

        if process is None:
            raise ValueError("Cannot terminate a process not owned by this manager")

        if process.poll() is not None:
            self._owned_processes.pop(pid, None)
            return False

        process.terminate()
        return True