from __future__ import annotations

import ctypes
import os
from pathlib import Path


def get_desktop_path() -> Path:
    """Resolve the real Windows Desktop, including OneDrive redirection.

    SHGetKnownFolderPath is the authoritative Windows API. Environment and home
    fallbacks make fixture tests and non-Windows development predictable.
    """
    override = os.getenv("WCA_DESKTOP_PATH")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        # FOLDERID_Desktop = B4BFCC3A-DB2C-424C-B029-7FE99A87C641
        import uuid
        from ctypes import wintypes

        folder_id = uuid.UUID("B4BFCC3A-DB2C-424C-B029-7FE99A87C641")
        path_ptr = ctypes.c_wchar_p()
        result = ctypes.windll.shell32.SHGetKnownFolderPath(  # type: ignore[attr-defined]
            ctypes.byref((ctypes.c_byte * 16).from_buffer_copy(folder_id.bytes_le)),
            0,
            wintypes.HANDLE(0),
            ctypes.byref(path_ptr),
        )
        if result == 0 and path_ptr.value:
            try:
                return Path(path_ptr.value).resolve()
            finally:
                ctypes.windll.ole32.CoTaskMemFree(path_ptr)  # type: ignore[attr-defined]
    return (Path.home() / "Desktop").resolve()


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_workspace_root() -> Path:
    return get_desktop_path() / "WebContent"


def runtime_root() -> Path:
    path = project_root() / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def windows_io_path(path: Path) -> Path:
    """Return an extended-length path for Windows file APIs when needed."""
    resolved = path.resolve()
    value = str(resolved)
    if os.name != "nt" or value.startswith("\\\\?\\") or len(value) < 248:
        return resolved
    if value.startswith("\\\\"):
        return Path(f"\\\\?\\UNC\\{value[2:]}")
    return Path(f"\\\\?\\{value}")


def path_exists(path: Path) -> bool:
    return windows_io_path(path).exists()


def ensure_runtime_dirs() -> None:
    for relative in ("logs", "failures", "state", "browser-cache"):
        (runtime_root() / relative).mkdir(parents=True, exist_ok=True)
