"""Installed application identity and an installer-visible lifetime guard."""
from contextlib import contextmanager
import ctypes
import sys
import time

SOURCE_APP_ID = "E7auto.E7auto"
INSTALLED_APP_ID = "E7auto.Installed"
RUNNING_MUTEX = r"Global\E7auto.Installed.Running"
SETUP_MUTEX = r"Global\E7auto.Installed.Setup"


def is_installed_build() -> bool:
    return bool(getattr(sys, "frozen", False) or "__compiled__" in globals())


def app_user_model_id() -> str:
    return INSTALLED_APP_ID if is_installed_build() else SOURCE_APP_ID


@contextmanager
def application_guard(*, wait_for_setup: bool = False):
    """Keep the mutex alive until every GUI/worker has exited; never kill a run."""
    if not is_installed_build():
        yield
        return
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    kernel.OpenMutexW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
    kernel.OpenMutexW.restype = ctypes.c_void_p
    def check_setup():
        mutex = kernel.OpenMutexW(0x00100000, False, SETUP_MUTEX)
        error = ctypes.get_last_error()
        if mutex:
            kernel.CloseHandle(mutex)
        if mutex or error == 5:
            raise RuntimeError("E7auto 正在安装或卸载，请完成后再启动。")
    deadline = time.monotonic() + 60
    while True:
        try:
            check_setup()
            break
        except RuntimeError:
            if not wait_for_setup or time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    handle = kernel.CreateMutexW(None, False, RUNNING_MUTEX)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        check_setup()
        yield
    finally:
        kernel.CloseHandle(handle)
