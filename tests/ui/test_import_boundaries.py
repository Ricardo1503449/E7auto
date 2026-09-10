import subprocess
import sys

from tests.helpers.paths import ROOT


def test_ui_import_keeps_wgc_deferred_to_worker() -> None:
    code = """
import sys
from e7auto.ui import MainWindow, StatsOverlay, AutomationWorker
assert MainWindow.__module__ == 'e7auto.ui.main_window'
assert StatsOverlay.__module__ == 'e7auto.ui.overlay'
assert AutomationWorker.__module__ == 'e7auto.ui.worker'
assert 'e7auto.wgc_capture' not in sys.modules
assert not any(name == 'winrt' or name.startswith('winrt.') for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT,
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
