from pathlib import Path

from PySide6.QtCore import QSignalBlocker

from .feature import _FeaturePage
from ..preferences import ContinuousRefreshPreferences
from ..widgets import _ToggleSwitch, _use_smooth_text


class _ShopFeaturePage(_FeaturePage):
    def __init__(self, project_root: Path, parent=None):
        super().__init__("刷新秘密商店", "shopFeaturePage", parent)
        self._continuous_refresh_preferences = ContinuousRefreshPreferences(project_root / "state" / "ui_preferences.json")
        self._continuous_refresh_enabled = self._continuous_refresh_preferences.load()
        self.add_limit("天空石消耗上限", "refreshLimitInput")
        self.friendship_toggle = _ToggleSwitch()
        self.add_row("购买友情点数", self.friendship_toggle)
        self.continuous_refresh_toggle = _ToggleSwitch(
            object_name="continuousRefreshToggle", accessible_name="连续刷新",
        )
        self.continuous_refresh_toggle.setChecked(self._continuous_refresh_enabled)
        self.continuous_refresh_toggle.toggled.connect(self._save_continuous_refresh)
        self.add_row("连续刷新", self.continuous_refresh_toggle)
        self.add_start_button("startButton")

    def _save_continuous_refresh(self, enabled: bool) -> None:
        try:
            self._continuous_refresh_preferences.save(enabled)
        except OSError as exc:
            with QSignalBlocker(self.continuous_refresh_toggle):
                self.continuous_refresh_toggle.setChecked(self._continuous_refresh_enabled)
            _use_smooth_text(self.status_label)
            self.show_status(f"无法保存连续刷新设置：{exc}")
            return
        self._continuous_refresh_enabled = enabled
        self.show_status("")
