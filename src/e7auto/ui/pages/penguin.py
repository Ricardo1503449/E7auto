from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from .feature import _FeaturePage
from ..styles import CONTROL_SIZE


class _PenguinFeaturePage(_FeaturePage):
    def __init__(self, parent=None):
        super().__init__("红叶换企鹅", "penguinFeaturePage", parent)
        self.add_limit("购买次数上限", "penguinPurchaseLimitInput")
        self.leaf_cost_display = QLabel()
        self.leaf_cost_display.setObjectName("penguinLeafCostDisplay")
        self.leaf_cost_display.setAccessibleName("预计消耗红叶")
        self.leaf_cost_display.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.leaf_cost_display.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.leaf_cost_display.setFixedSize(*CONTROL_SIZE)
        self.leaf_cost_display.setAccessibleDescription("按每次购买消耗 5100 红叶计算")
        self.add_row("预计消耗红叶", self.leaf_cost_display)
        self.limit_input.textChanged.connect(self._update_leaf_cost)
        self._update_leaf_cost()
        self.add_start_button("penguinStartButton")

    def _update_leaf_cost(self) -> None:
        limit = self.limit_value()
        self.leaf_cost_display.setText(f"{limit * 5100:,}" if limit is not None else "—")
