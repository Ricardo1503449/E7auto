"""Background file/folder/browser operations with a completion lifetime."""
from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread

from e7auto.platform.desktop import open_default


class DesktopActionThread(QThread):
    def __init__(self, target: str | Path, parent=None, *, create_directory: bool = True):
        super().__init__(parent)
        self.target = target
        self.create_directory = create_directory
        self._open = open_default
        self.cancelled = Event()
        self.error = ""

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> None:
        try:
            if self.cancelled.is_set():
                return
            if isinstance(self.target, Path):
                if self.create_directory:
                    self.target.mkdir(parents=True, exist_ok=True)
                elif not self.target.is_file():
                    raise FileNotFoundError(f"文件不存在：{self.target}")
            if not self.cancelled.is_set():
                self._open(str(self.target))
        except Exception as exc:
            self.error = f"无法打开，请检查文件或目录权限以及系统默认打开方式。\n{type(exc).__name__}: {exc}"
