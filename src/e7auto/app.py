from __future__ import annotations

import ctypes
import os
import json
import platform
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .platform_windows import enable_per_monitor_dpi_awareness
from .ui import MainWindow


def set_windows_app_user_model_id() -> None:
    setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
    setter.argtypes = [ctypes.c_wchar_p]
    setter.restype = ctypes.c_long
    result = setter("E7auto.E7auto")
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


def main() -> int:
    if os.name != "nt":
        raise RuntimeError("E7auto supports Windows x64 only")
    root = project_root()
    validate_source_environment(root)
    if "--self-check" in sys.argv:
        compiled = bool(getattr(sys, "frozen", False) or "__compiled__" in globals())
        config_path = root / "config" / "internal.yaml"
        templates_path = root / "assets" / "templates"
        ui_assets_path = root / "assets" / "ui"
        result = {
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
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return (
            0
            if result["config_present"]
            and result["templates_present"]
            and result["ui_assets_present"]
            and not result["venv_bundled"]
            else 2
        )
    enable_per_monitor_dpi_awareness()
    set_windows_app_user_model_id()
    application = QApplication(sys.argv)
    application.setApplicationName("E7auto")
    application.setWindowIcon(QIcon(str(root / "assets" / "ui" / "e7auto.ico")))
    window = MainWindow(root)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
