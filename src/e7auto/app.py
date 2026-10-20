from __future__ import annotations

import ctypes
import os
import json
import platform
import sys
from threading import Thread
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from e7auto import __version__
from e7auto.platform.installation import application_guard, app_user_model_id
from e7auto.platform.windows import enable_per_monitor_dpi_awareness
from e7auto.ui import MainWindow


def set_windows_app_user_model_id() -> None:
    setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
    setter.argtypes = [ctypes.c_wchar_p]
    setter.restype = ctypes.c_long
    result = setter(app_user_model_id())
    if result != 0:
        raise OSError(result, "Unable to set the Windows application identity")


def project_root() -> Path:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def validate_source_environment(root: Path) -> None:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return
    expected = (root / ".venv").resolve()
    if Path(sys.prefix).resolve() != expected:
        raise RuntimeError(
            f"Source mode must run with {expected}\\Scripts\\python.exe; current prefix is {sys.prefix}"
        )


def validate_wgc_import() -> tuple[bool, str]:
    try:
        from e7auto.platform.wgc_capture import WindowsGraphicsCaptureService

        if WindowsGraphicsCaptureService is None:
            raise RuntimeError("WGC capture service is unavailable")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("E7auto supports Windows x64 only")
    root = project_root()
    validate_source_environment(root)
    if "--self-check" in sys.argv or "--self-check-statistics-https" in sys.argv:
        from e7auto.ui.reporting_check import check_reporting_runtime

        compiled = bool(getattr(sys, "frozen", False) or "__compiled__" in globals())
        wgc_importable, wgc_import_error = validate_wgc_import()
        config_path = root / "config" / "internal.yaml"
        templates_path = root / "assets" / "templates"
        ui_assets_path = root / "assets" / "ui"
        result = {
            "version": __version__,
            "compiled": compiled,
            "machine": platform.machine(),
            "config_present": config_path.is_file(),
            "templates_present": templates_path.is_dir(),
            "ui_assets_present": all(
                (ui_assets_path / name).is_file()
                for name in (
                    "e7auto.ico",
                    "e7auto-icon-256.png",
                    "shop-card-background.png",
                )
            ),
            "venv_bundled": compiled and (root / ".venv").exists(),
            "wgc_importable": wgc_importable,
            "wgc_import_error": wgc_import_error,
        }
        result.update(check_reporting_runtime(root,
            include_https="--self-check-statistics-https" in sys.argv))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return (
            0
            if result["config_present"]
            and result["templates_present"]
            and result["ui_assets_present"]
            and not result["venv_bundled"]
            and result["wgc_importable"]
            and result["statistics_runtime_ready"]
            else 2
        )
    enable_per_monitor_dpi_awareness()
    set_windows_app_user_model_id()
    application = QApplication(sys.argv)
    application.setApplicationName("E7auto")
    application.setWindowIcon(QIcon(str(root / "assets" / "ui" / "e7auto.ico")))
    update_job = next((arg.removeprefix('--after-update=') for arg in sys.argv
                       if arg.startswith('--after-update=')), None)
    with application_guard(wait_for_setup=update_job is not None):
        window = MainWindow(root)
        window.show()
        if update_job is not None:
            def cleanup_update():
                from e7auto.platform.update_install import cleanup_applied_update
                try:
                    cleanup_applied_update(root, update_job)
                except (OSError, ValueError, IndexError, TypeError) as exc:
                    print(f'更新缓存清理未完成：{exc}', file=sys.stderr)
            def start_update_cleanup():
                Thread(target=cleanup_update, name='update-cache-cleanup', daemon=True).start()
            QTimer.singleShot(0, start_update_cleanup)
        return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
