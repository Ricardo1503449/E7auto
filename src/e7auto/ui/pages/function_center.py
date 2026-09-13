from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..widgets import ModuleCardSpec, _ModuleCard


class _FunctionCenterPage(QWidget):
    module_requested = Signal(str)

    def __init__(self, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("functionCenterPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(52, 36, 52, 42)
        layout.setSpacing(24)

        heading = QLabel("功能中心")
        heading.setObjectName("pageHeading")
        layout.addWidget(heading)

        self._scroll = QScrollArea()
        self._scroll.setObjectName("moduleScrollArea")
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setAutoFillBackground(False)
        self._scroll.viewport().setAutoFillBackground(False)

        self._card_host = QWidget()
        self._card_host.setObjectName("moduleCardHost")
        self._grid = QGridLayout(self._card_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(28)
        self._grid.setVerticalSpacing(28)
        self._scroll.setWidget(self._card_host)
        layout.addWidget(self._scroll, 1)

        specs = (
            ModuleCardSpec(
                "shop_refresh",
                "刷新秘密商店",
                "shop-card-background.png",
                True,
            ),
            ModuleCardSpec("penguin_exchange", "红叶换企鹅", "penguin-card-background.png", True),
            ModuleCardSpec("future_2", None, None, False),
            ModuleCardSpec("future_3", None, None, False),
        )
        self._cards_by_id: dict[str, _ModuleCard] = {}
        self._cards: list[_ModuleCard] = []
        for index, spec in enumerate(specs):
            image_path = (
                project_root / "assets" / "ui" / spec.image_filename
                if spec.image_filename is not None
                else None
            )
            object_name = (
                "shopModuleCard"
                if spec.module_id == "shop_refresh"
                else "penguinModuleCard" if spec.module_id == "penguin_exchange"
                else f"futureModuleCard{index}"
            )
            card = _ModuleCard(
                title=spec.title,
                image_path=image_path,
                available=spec.available,
                object_name=object_name,
            )
            if spec.available:
                card.clicked.connect(
                    lambda _checked=False, module_id=spec.module_id: self.module_requested.emit(
                        module_id
                    )
                )
            self._cards_by_id[spec.module_id] = card
            self._cards.append(card)
        self.shop_card = self._cards_by_id["shop_refresh"]
        self.penguin_card = self._cards_by_id["penguin_exchange"]
        self._column_count = 0
        self._apply_columns(2)

    @property
    def cards(self) -> tuple[_ModuleCard, ...]:
        return tuple(self._cards)

    def _apply_columns(self, columns: int) -> None:
        if columns == self._column_count:
            return
        while self._grid.count():
            self._grid.takeAt(0)
        for index, card in enumerate(self._cards):
            self._grid.addWidget(card, index // columns, index % columns)
        for column in range(2):
            self._grid.setColumnStretch(column, 1 if column < columns else 0)
        self._column_count = columns

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._apply_columns(1 if event.size().width() < 820 else 2)
        super().resizeEvent(event)
