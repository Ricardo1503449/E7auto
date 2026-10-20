"""Run-end notification presentation and GUI-thread delivery."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from e7auto.core.domain import RuntimeSnapshot, StopReason


_FEATURE_NAMES = {"shop_refresh": "刷新秘密商店", "penguin_exchange": "红叶换企鹅", "chaos": "自动卡厄思"}
_COMPLETED = {StopReason.BUDGET_COMPLETE, StopReason.PENGUIN_LIMIT_COMPLETE, StopReason.CHAOS_COMPLETE}
_EXPECTED = {StopReason.REFRESH_STRATEGY_EXHAUSTED, StopReason.PURCHASE_FUNDS_INSUFFICIENT,
             StopReason.PENGUIN_FUNDS_COMPLETE}
_REASONS = {
    StopReason.BUDGET_COMPLETE: "已达到天空石消耗上限",
    StopReason.REFRESH_STRATEGY_EXHAUSTED: "已达到连续未出货的刷新策略上限",
    StopReason.PURCHASE_FUNDS_INSUFFICIENT: "购买资金不足",
    StopReason.PENGUIN_LIMIT_COMPLETE: "已达到购买次数上限",
    StopReason.PENGUIN_FUNDS_COMPLETE: "红叶不足，兑换已结束",
    StopReason.CHAOS_COMPLETE: "已完成设定轮数",
    StopReason.WINDOW_ABNORMAL: "游戏窗口状态异常",
    StopReason.INVALID_DISPLAY_GEOMETRY: "游戏显示区域异常",
    StopReason.UNSUPPORTED_DISPLAY_RESOLUTION: "不支持当前游戏分辨率",
    StopReason.DISPLAY_CHANGED: "游戏显示区域发生变化",
    StopReason.PERMISSION_REQUIRED: "运行权限不足",
    StopReason.CAPTURE_FAILURE: "游戏画面采集失败",
    StopReason.INPUT_FAILURE: "操作发送失败",
    StopReason.RECOGNITION_TIMEOUT: "等待画面识别超时",
    StopReason.REFRESH_CLICK_UNACKNOWLEDGED: "无法确认刷新操作的结果",
    StopReason.PURCHASE_RESULT_AMBIGUOUS: "无法确认购买结果",
    StopReason.REFRESH_BALANCE_MISMATCH: "刷新后的余额与预期不符",
    StopReason.SCROLL_VERIFICATION_FAILED: "无法确认商店滚动结果",
    StopReason.CONFIG_INCOMPLETE: "配置或资源不完整",
    StopReason.ENTRY_FAILURE: "无法进入目标功能界面",
    StopReason.INTERNAL_ERROR: "程序运行异常，请查看日志",
    StopReason.CHAOS_FAILED: "卡厄思挑战失败",
    StopReason.CHAOS_UNSUPPORTED_STAGE: "不支持当前卡厄思阶段",
    StopReason.CHAOS_STAGE_SWITCH_FAILED: "卡厄思阶段切换失败",
    StopReason.CHAOS_UNKNOWN_CONTENT: "遇到无法识别的卡厄思内容",
    StopReason.CHAOS_BATTLE_TIMEOUT: "战斗等待超时",
}


def completion_message(snapshot: RuntimeSnapshot) -> tuple[str, str] | None:
    reason = snapshot.stop_reason
    if not snapshot.is_final or reason is None or reason == StopReason.MANUAL_STOP:
        return None
    outcome = "已完成" if reason in _COMPLETED else "已停止" if reason in _EXPECTED else "异常停止"
    name = _FEATURE_NAMES.get(snapshot.feature_id, "脚本")
    if snapshot.feature_id == "chaos":
        progress = f"已完成 {snapshot.rounds_completed}/{snapshot.round_limit} 轮"
    elif snapshot.feature_id == "penguin_exchange":
        progress = f"已购买 {snapshot.purchases_completed}/{snapshot.purchase_limit} 次"
    else:
        progress = f"已消耗天空石：{snapshot.refresh_spent}"
    return (f"E7auto · {name}{outcome}",
            f"{_REASONS.get(reason, '运行异常，请查看日志')}\n{progress}")


class RunEndNotifier(QObject):
    def __init__(self, icon_path: Path, parent: QObject) -> None:
        super().__init__(parent)
        self._icon_path = icon_path
        self._native = None
        self._last_run_id: str | None = None

    def notify(self, snapshot: RuntimeSnapshot, *, enabled: bool) -> None:
        if not snapshot.is_final or snapshot.run_id == self._last_run_id:
            return
        self._last_run_id = snapshot.run_id
        content = completion_message(snapshot)
        if enabled and content is not None:
            self._deliver(*content)

    def startup_failed(self, feature: str, *, enabled: bool) -> None:
        if enabled:
            name = _FEATURE_NAMES.get({"shop": "shop_refresh", "penguin": "penguin_exchange"}.get(feature, feature), "脚本")
            self._deliver(f"E7auto · {name}启动失败", "未能启动脚本，请查看功能页中的错误信息。")

    def _deliver(self, title: str, message: str) -> None:
        try:
            self._send(title, message)
        except Exception:
            # An unavailable Windows shell cannot turn completion into a failure.
            logging.getLogger(__name__).warning("运行结束通知发送失败", exc_info=True)

    def _send(self, title: str, message: str) -> None:
        if QApplication.platformName() != "windows":
            return
        if self._native is None:
            from e7auto.platform.notifications import WindowsNotifier
            self._native = WindowsNotifier(self._icon_path)
        self._native.show(title, message)
