import json
from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from e7auto.ui import MainWindow
from e7auto.ui.preferences import (
    AnnouncementPreferences,
    ContinuousRefreshPreferences,
    NotificationPreferences,
    ShopStatisticsPreferences,
)
from tests.helpers.paths import ROOT


@pytest.mark.parametrize("raw", [None, b"broken", b"[]", b"{}",
                                  b'{"shop_continuous_refresh": "true"}',
                                  b'{"shop_continuous_refresh": 1}',
                                  b'{"shop_continuous_refresh": null}'])
def test_missing_or_invalid_refresh_preference_defaults_off_without_writing(tmp_path, raw):
    state = tmp_path / "ui_preferences.json"
    if raw is not None:
        state.write_bytes(raw)
    assert ContinuousRefreshPreferences(state).load() is False
    assert state.read_bytes() == raw if raw is not None else not state.exists()


@pytest.mark.parametrize("enabled", [True, False])
def test_refresh_selection_survives_window_close_and_a_new_process(tmp_path, qt_application, enabled):
    state = tmp_path / "state/ui_preferences.json"
    window = MainWindow(tmp_path)
    window._show_module_page("shop_refresh")
    window.show()
    qt_application.processEvents()
    page = window._shop_feature_page
    assert not page.continuous_refresh_toggle.isChecked()
    QTest.mouseClick(page.continuous_refresh_toggle, Qt.MouseButton.LeftButton)
    if not enabled:
        QTest.mouseClick(page.continuous_refresh_toggle, Qt.MouseButton.LeftButton)
    page.friendship_toggle.setChecked(True)
    page.limit_input.setText("300")
    assert json.loads(state.read_text(encoding="utf-8"))["shop_continuous_refresh"] is enabled
    window.close()
    before = state.read_bytes()

    restored = MainWindow(tmp_path)
    assert restored._shop_feature_page.continuous_refresh_toggle.isChecked() is enabled
    assert not restored._shop_feature_page.friendship_toggle.isChecked()
    assert restored._shop_feature_page.limit_input.text() == "0"
    restored.close()
    code = """
from pathlib import Path
import sys
from PySide6.QtWidgets import QApplication
from e7auto.ui.pages.shop import _ShopFeaturePage
app = QApplication([])
page = _ShopFeaturePage(Path(sys.argv[1]))
assert page.continuous_refresh_toggle.isChecked() is (sys.argv[2] == 'true')
assert not page.friendship_toggle.isChecked()
page.close()
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path), str(enabled).lower()],
                            cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert state.read_bytes() == before


def test_refresh_saves_preserve_other_preferences_and_their_later_changes(tmp_path):
    state = tmp_path / "state/ui_preferences.json"
    state.parent.mkdir()
    values = {"run_end_notifications": False, "shop_statistics": False, "shop_statistics_generation": 3,
              "announcement_seen_id": "usage-guide-v1", "future_setting": 42}
    state.write_text(json.dumps(values), encoding="utf-8")
    before = state.read_bytes()
    window = MainWindow(tmp_path)
    assert state.read_bytes() == before
    toggle = window._shop_feature_page.continuous_refresh_toggle
    toggle.click()
    values["shop_continuous_refresh"] = True
    assert json.loads(state.read_text(encoding="utf-8")) == values

    NotificationPreferences(state).save(True)
    ShopStatisticsPreferences(state).save(True)
    AnnouncementPreferences(state).save("usage-guide-v2")
    toggle.click()
    values.update(run_end_notifications=True, shop_statistics=True,
                  announcement_seen_id="usage-guide-v2", shop_continuous_refresh=False)
    assert json.loads(state.read_text(encoding="utf-8")) == values
    window.close()


@pytest.mark.parametrize("initial", [False, True])
def test_failed_atomic_save_reverts_toggle_preserves_file_and_allows_retry(tmp_path, qt_application, monkeypatch, initial):
    state = tmp_path / "state/ui_preferences.json"
    state.parent.mkdir()
    state.write_text(json.dumps({"shop_continuous_refresh": initial, "future_setting": 42}), encoding="utf-8")
    before = state.read_bytes()
    window = MainWindow(tmp_path)
    window._show_module_page("shop_refresh")
    window.show()
    qt_application.processEvents()
    page = window._shop_feature_page
    replace = Path.replace
    attempts = []

    def fail_preferences_replace(source, target):
        if source == state.with_suffix(".json.tmp"):
            attempts.append(target)
            raise PermissionError("read only")
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_preferences_replace)
        page.continuous_refresh_toggle.click()
        assert page.continuous_refresh_toggle.isChecked() is initial
        assert state.read_bytes() == before and len(attempts) == 1
        assert page.status_label.isVisible() and "无法保存连续刷新设置" in page.status_label.text()

    page.continuous_refresh_toggle.click()
    assert page.continuous_refresh_toggle.isChecked() is not initial
    assert json.loads(state.read_text(encoding="utf-8")) == {
        "shop_continuous_refresh": not initial, "future_setting": 42,
    }
    assert not page.status_label.text()
    window.close()
