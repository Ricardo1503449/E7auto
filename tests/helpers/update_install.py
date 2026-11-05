"""Isolate Windows installation identity while retaining the production validator."""
from contextlib import nullcontext
from pathlib import Path

from e7auto.platform import update_install


def configure_installed_update(root: Path, monkeypatch) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'install-files.txt').write_text(
        update_install.PRODUCT_ID + '\n', encoding='utf-8-sig')
    monkeypatch.setattr(update_install, 'is_installed_build', lambda: True)
    monkeypatch.setattr(update_install.win32api, 'GetModuleFileName',
                        lambda _module: str(root / 'E7auto.exe'))
    monkeypatch.setattr(update_install.winreg, 'OpenKey', lambda *args: nullcontext(object()))
    monkeypatch.setattr(update_install.winreg, 'QueryValueEx', lambda *args: (str(root), 1))
