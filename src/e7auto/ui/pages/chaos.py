from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QComboBox, QListView, QProxyStyle, QStyle, QStyleOptionComboBox, QStylePainter,
)

from .feature import _FeaturePage
from ..styles import CONTROL_SIZE

from e7auto.features.chaos.lineups import LINEUPS


class _StageComboStyle(QProxyStyle):
    def pixelMetric(self, metric, option=None, widget=None):
        if (metric == QStyle.PixelMetric.PM_MenuButtonIndicator and widget is not None
                and widget.font().pixelSize() > 0 and widget.font().pointSize() <= 0):
            # Windows 11's style passes pointSize() == -1 to its icon font for
            # pixel-sized widgets. Use its default indicator metric instead.
            return super().pixelMetric(metric, option, None)
        return super().pixelMetric(metric, option, widget)


class _StageComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chaosStageInput")
        self.setProperty("chaosSetting", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMaxVisibleItems(5)
        if QApplication.style().objectName() == "windows11":
            # Create an owned style instance; never transfer ownership of app.style().
            self._stage_style = _StageComboStyle("windows11")
            self._stage_style.setParent(self)
            self.setStyle(self._stage_style)
        view = QListView(self)
        view.setObjectName("chaosStageList")
        view.setProperty("chaosSetting", True)
        view.setUniformItemSizes(True)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setView(view)

    def showPopup(self):
        self.ensurePolished()
        self.view().setFont(self.font())
        self.view().ensurePolished()
        # Escape may leave the view's preview row different from the committed value.
        self.view().setCurrentIndex(self.model().index(
            self.currentIndex(), self.modelColumn(), self.rootModelIndex(),
        ))
        # Qt's combo scroll effect is chosen synchronously inside showPopup.
        # Suppress it for this popup only, then restore the application's setting.
        effect = Qt.UIEffect.UI_AnimateCombo
        animated = QApplication.isEffectEnabled(effect)
        try:
            QApplication.setEffectEnabled(effect, False)
            super().showPopup()
        finally:
            QApplication.setEffectEnabled(effect, animated)

    def paintEvent(self, event):
        del event
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        # The stylesheet owns the complete frame; don't mix in a native arrow button.
        option.subControls = QStyle.SubControl.SC_ComboBoxFrame
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        arrow_rect = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option,
            QStyle.SubControl.SC_ComboBoxArrow, self,
        )
        # Reserve equal space on both sides so the caption is centered in the
        # whole field, rather than in the native edit area left of the arrow.
        inset = arrow_rect.width()+8
        text_rect = self.rect().adjusted(inset, 0, -inset, 0)
        painter.drawItemText(text_rect, Qt.AlignmentFlag.AlignCenter, option.palette,
                             self.isEnabled(), self.currentText(), QPalette.ColorRole.ButtonText)
        arrow = arrow_rect.center()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#454c54" if self.isEnabled() else "#a3a8ae"), 1.7,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPolyline(QPolygonF([
            QPointF(arrow.x()-5, arrow.y()-2), QPointF(arrow.x(), arrow.y()+3),
            QPointF(arrow.x()+5, arrow.y()-2),
        ]))


class _ChaosFeaturePage(_FeaturePage):
    def __init__(self, parent=None):
        super().__init__("自动卡厄思", "chaosFeaturePage", parent)
        self.stage_input = _StageComboBox()
        for stage in range(1, 6):
            self.stage_input.addItem(f"第{stage}阶段", stage)
            self.stage_input.setItemData(stage - 1, Qt.AlignmentFlag.AlignCenter, Qt.ItemDataRole.TextAlignmentRole)
        self.stage_input.setFixedSize(*CONTROL_SIZE)
        self.add_row("目标阶段", self.stage_input)
        self.lineup_input = _StageComboBox()
        self.lineup_input.setObjectName("chaosLineupInput")
        self.lineup_input.view().setObjectName("chaosLineupList")
        self.lineup_input.setAccessibleName("阵容")
        for lineup in LINEUPS:
            self.lineup_input.addItem(lineup.label, lineup.key)
        self.lineup_input.setFixedSize(*CONTROL_SIZE)
        self.add_row("阵容", self.lineup_input)
        self.add_limit("完成轮数", "chaosRoundLimitInput", default=1, minimum=1)
        self.add_start_button("chaosStartButton")

    def target_stage(self) -> int:
        return self.stage_input.currentData()

    def lineup_id(self) -> str:
        return self.lineup_input.currentData()
