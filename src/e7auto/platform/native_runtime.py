"""Select the MSVC runtime shared by Qt and PyWinRT before loading WGC."""
from __future__ import annotations

import ctypes
from importlib.util import find_spec
from pathlib import Path
import sys
import threading

import win32api


# The locked Qt/Shiboken wheel ships this runtime. WinRT 3.2.1 ships 14.29,
# which must not win the process-wide MSVCP140 dependency lookup.
MINIMUM_MSVC_VERSION = (14, 44, 35211, 0)
MSVC_FILES = (
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "msvcp140_codecvt_ids.dll", "concrt140.dll",
    "vcruntime140.dll", "vcruntime140_1.dll",
)
_lock = threading.Lock()
_runtime_library = None


def dll_version(path: Path) -> tuple[int, int, int, int]:
    info = win32api.GetFileVersionInfo(str(path), "\\")
    high, low = info["FileVersionMS"], info["FileVersionLS"]
    # pywin32 may expose DWORDs as signed integers (build numbers can exceed 32767).
    return (high >> 16) & 0xFFFF, high & 0xFFFF, (low >> 16) & 0xFFFF, low & 0xFFFF


def loaded_runtime_path() -> Path | None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_handle = kernel.GetModuleHandleW
    get_handle.argtypes = [ctypes.c_wchar_p]
    get_handle.restype = ctypes.c_void_p
    handle = get_handle("msvcp140.dll")
    if not handle:
        return None
    get_name = kernel.GetModuleFileNameW
    get_name.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    get_name.restype = ctypes.c_uint
    name = ctypes.create_unicode_buffer(32768)
    length = get_name(handle, name, len(name))
    if not length or length >= len(name):
        raise RuntimeError("Cannot resolve the loaded MSVCP140 runtime")
    return Path(name.value).resolve()


def runtime_distribution_files() -> dict[str, Path]:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        directory = Path(sys.executable).resolve().parent
    else:
        # Locating the wheel must not import Qt or Shiboken as a side effect.
        spec = find_spec("shiboken6")
        if spec is None or not spec.origin:
            raise RuntimeError("The locked shiboken6 runtime distribution is missing")
        directory = Path(spec.origin).resolve().parent
    files = {name: directory / name for name in MSVC_FILES}
    for name, path in files.items():
        if not path.is_file() or dll_version(path) < MINIMUM_MSVC_VERSION:
            raise RuntimeError(f"Missing or outdated compatible MSVC runtime: {path}")
    return files


def _check_loaded(path: Path) -> dict[str, object]:
    version = dll_version(path)
    if version < MINIMUM_MSVC_VERSION:
        raise RuntimeError(
            f"Incompatible MSVCP140 runtime already loaded: {path} ({version}); "
            f"requires >= {MINIMUM_MSVC_VERSION}. Restart through the E7auto entry point; "
            "an in-use native runtime cannot be safely replaced."
        )
    return {"path": str(path), "version": version}


def ensure_msvc_runtime() -> dict[str, object]:
    """Pin a compatible DLL without changing PATH, installed wheels or COM state."""
    global _runtime_library
    with _lock:
        loaded = loaded_runtime_path()
        if loaded is not None:
            return _check_loaded(loaded)
        path = runtime_distribution_files()["msvcp140.dll"]
        # Absolute path; dependencies search the selected directory and trusted defaults.
        _runtime_library = ctypes.WinDLL(str(path), winmode=0x00001100)
        loaded = loaded_runtime_path()
        if loaded is None:
            raise RuntimeError("Compatible MSVCP140 runtime did not load")
        return _check_loaded(loaded)
