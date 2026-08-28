from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import yaml
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from e7auto.app import validate_source_environment
from e7auto.config import Rect, Size, TargetConfig
from e7auto.platform_windows import (
    Win32WindowService,
    enable_per_monitor_dpi_awareness,
)
from e7auto.ports import WindowRef, WindowState
from e7auto.ui import StatsOverlay


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "internal.yaml"


@dataclass(frozen=True, slots=True)
class PositionCalibrationConfig:
    executable_path: Path
    window_title: str
    baseline_client_size: Size
    targets: tuple[TargetConfig, ...]


def position_calibration_initial_state_is_valid(
    state: WindowState,
    expected_size: Size,
) -> bool:
    return (
        state.exists
        and not state.minimized
        and state.foreground
        and (state.client_bounds.width, state.client_bounds.height)
        == (expected_size.width, expected_size.height)
    )


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{name} must be a mapping")
    return value


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError(f"{name} must be an integer")
    return value


def load_position_calibration_config(
    path: Path = CONFIG_PATH,
) -> PositionCalibrationConfig:
    """Load only the already-confirmed fields required for position calibration."""

    raw = yaml.safe_load(path.resolve().read_text(encoding="utf-8"))
    root = _mapping(raw, "root")
    if root.get("schema_version") != 1:
        raise RuntimeError("schema_version must be 1")

    game = _mapping(root.get("game"), "game")
    executable_path = Path(str(game.get("executable_path", "")))
    window_title = game.get("window_title")
    size = _mapping(game.get("baseline_client_size"), "game.baseline_client_size")
    baseline = Size(
        _integer(size.get("width"), "game.baseline_client_size.width"),
        _integer(size.get("height"), "game.baseline_client_size.height"),
    )
    if (
        executable_path.suffix.casefold() != ".exe"
        or (not executable_path.is_absolute() and executable_path.name != str(executable_path))
    ):
        raise RuntimeError(
            "position-calibration executable must be an absolute .exe path or an .exe filename"
        )
    if not isinstance(window_title, str) or not window_title:
        raise RuntimeError("position-calibration window title is required")
    if baseline != Size(2322, 1306):
        raise RuntimeError(f"unexpected position-calibration baseline: {baseline}")

    targets_raw = root.get("targets")
    if not isinstance(targets_raw, list) or not targets_raw:
        raise RuntimeError("position calibration requires configured target rows")
    targets: list[TargetConfig] = []
    for index, value in enumerate(targets_raw):
        target = _mapping(value, f"targets[{index}]")
        fields = {
            name: target.get(name)
            for name in (
                "id",
                "display_name",
                "template",
                "confirm_template",
                "purchased_template",
            )
        }
        if any(not isinstance(item, str) or not item for item in fields.values()):
            raise RuntimeError(f"targets[{index}] has an invalid string field")
        selectable = target.get("user_selectable", False)
        if not isinstance(selectable, bool):
            raise RuntimeError(f"targets[{index}].user_selectable must be a boolean")
        targets.append(
            TargetConfig(
                str(fields["id"]),
                str(fields["display_name"]),
                str(fields["template"]),
                str(fields["confirm_template"]),
                str(fields["purchased_template"]),
                selectable,
            )
        )
    if len({target.target_id for target in targets}) != len(targets):
        raise RuntimeError("position-calibration target ids must be unique")

    return PositionCalibrationConfig(
        executable_path,
        window_title,
        baseline,
        tuple(targets),
    )


def confirmed_result(
    client_bounds: Rect,
    overlay: StatsOverlay,
) -> dict[str, object]:
    offset = overlay.position_calibration_offset()
    geometry = overlay.frameGeometry()
    return {
        "status": "confirmed",
        "client_bounds": {
            "x": client_bounds.x,
            "y": client_bounds.y,
            "width": client_bounds.width,
            "height": client_bounds.height,
        },
        "overlay_bounds": {
            "x": geometry.x(),
            "y": geometry.y(),
            "width": geometry.width(),
            "height": geometry.height(),
        },
        "offset": {"x": offset.x(), "y": offset.y()},
    }


class PositionCalibrationWindow(QWidget):
    def __init__(
        self,
        config: PositionCalibrationConfig,
        window_service: Win32WindowService,
        game_window: WindowRef,
        client_bounds: Rect,
        overlay: StatsOverlay,
        result_path: Path,
    ) -> None:
        super().__init__(None, Qt.WindowType.WindowStaysOnTopHint)
        self._config = config
        self._window_service = window_service
        self._game_window = game_window
        self._client_bounds = client_bounds
        self._overlay = overlay
        self._result_path = result_path
        self._finished = False

        self.setWindowTitle("E7auto 第一阶段位置标定")
        self.setFixedWidth(400)
        layout = QVBoxLayout(self)
        instructions = QLabel(
            "拖动黑色悬浮窗到目标位置。\n"
            "这里只标定位置，不发送任何游戏输入。"
        )
        instructions.setWordWrap(True)
        self._coordinates = QLabel()
        self._coordinates.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._status = QLabel("游戏客户区和悬浮窗尺寸已锁定。")
        self._status.setWordWrap(True)
        self._confirm = QPushButton("确认此位置")
        cancel = QPushButton("取消")
        self._confirm.clicked.connect(self._confirm_position)
        cancel.clicked.connect(self._cancel)
        layout.addWidget(instructions)
        layout.addWidget(self._coordinates)
        layout.addWidget(self._status)
        layout.addWidget(self._confirm)
        layout.addWidget(cancel)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._refresh_state)
        self._timer.start()
        self._refresh_state()

    def place_next_to_client(self) -> None:
        self.adjustSize()
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        right_x = self._client_bounds.x + self._client_bounds.width + 16
        left_x = self._client_bounds.x - self.width() - 16
        if right_x + self.width() <= available.right() + 1:
            x = right_x
        elif left_x >= available.left():
            x = left_x
        else:
            x = max(available.left(), self._client_bounds.x + 16)
        y = min(
            max(available.top(), self._client_bounds.y),
            available.bottom() - self.height() + 1,
        )
        self.move(x, y)

    def _client_is_unchanged(self) -> tuple[bool, str]:
        state = self._window_service.inspect(self._game_window)
        if not state.exists:
            return False, "游戏窗口已关闭，不能确认。"
        if state.minimized:
            return False, "游戏窗口已最小化，不能确认。"
        if state.client_bounds != self._client_bounds:
            return False, "游戏客户区位置或尺寸已改变，请取消后重新开始。"
        return True, "游戏客户区和悬浮窗尺寸已锁定。"

    def _refresh_state(self) -> None:
        offset = self._overlay.position_calibration_offset()
        self._coordinates.setText(
            f"客户区左上角：({self._client_bounds.x}, {self._client_bounds.y})\n"
            f"当前相对偏移：({offset.x()}, {offset.y()})\n"
            f"悬浮窗尺寸：{self._overlay.width()} x {self._overlay.height()}"
        )
        valid, message = self._client_is_unchanged()
        self._status.setText(message)
        self._status.setStyleSheet("color: #b00020;" if not valid else "")
        self._confirm.setEnabled(valid)

    def _write_result(self, payload: dict[str, object]) -> None:
        self._result_path.parent.mkdir(parents=True, exist_ok=True)
        self._result_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _confirm_position(self) -> None:
        valid, message = self._client_is_unchanged()
        if not valid:
            self._status.setText(message)
            self._confirm.setEnabled(False)
            return
        payload = confirmed_result(self._client_bounds, self._overlay)
        self._finished = True
        self._timer.stop()
        self._overlay.finish_position_calibration()
        self._write_result(payload)
        self._overlay.close()
        QApplication.exit(0)

    def _cancel(self) -> None:
        self._finish_cancelled()
        QApplication.exit(1)

    def _finish_cancelled(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._timer.stop()
        self._overlay.finish_position_calibration()
        self._write_result({"status": "cancelled"})
        self._overlay.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._finish_cancelled()
        super().closeEvent(event)


def _validated_result_path(value: str) -> Path:
    result = Path(value).resolve()
    if not result.is_relative_to(ROOT.resolve()):
        raise argparse.ArgumentTypeError("result path must stay inside the project")
    return result


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate the production overlay position")
    parser.add_argument("--result", required=True, type=_validated_result_path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    validate_source_environment(ROOT)
    enable_per_monitor_dpi_awareness()
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("E7auto overlay position calibration")

    try:
        config = load_position_calibration_config()
        windows = Win32WindowService()
        game_window = windows.locate_unique(
            str(config.executable_path),
            config.window_title,
        )
        state = windows.inspect(game_window)
        expected = config.baseline_client_size
        if not position_calibration_initial_state_is_valid(state, expected):
            raise RuntimeError(
                "game must be foreground and its client must be exactly "
                f"{expected.width} x {expected.height}, got "
                f"state={state}"
            )

        overlay = StatsOverlay()
        overlay.begin_position_calibration(config.targets, state.client_bounds)
        control = PositionCalibrationWindow(
            config,
            windows,
            game_window,
            state.client_bounds,
            overlay,
            args.result,
        )
        control.show()
        control.place_next_to_client()
        control.raise_()
        return application.exec()
    except Exception as exc:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(
            json.dumps(
                {"status": "error", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        QMessageBox.critical(None, "位置标定无法启动", str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
