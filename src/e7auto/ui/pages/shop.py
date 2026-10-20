from .feature import _FeaturePage
from ..widgets import _ToggleSwitch


class _ShopFeaturePage(_FeaturePage):
    def __init__(self, parent=None):
        super().__init__("刷新秘密商店", "shopFeaturePage", parent)
        self.add_limit("天空石消耗上限", "refreshLimitInput")
        self.friendship_toggle = _ToggleSwitch()
        self.add_row("购买友情点数", self.friendship_toggle)
        self.continuous_refresh_toggle = _ToggleSwitch(
            object_name="continuousRefreshToggle", accessible_name="连续刷新",
        )
        self.add_row("连续刷新", self.continuous_refresh_toggle)
        self.add_start_button("startButton")
