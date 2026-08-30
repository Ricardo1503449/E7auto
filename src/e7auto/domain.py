from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class RunState(str, Enum):
    IDLE = "idle"
    PREPARING = "preparing"
    ENTERING_STORE = "entering_store"
    SCANNING_TOP = "scanning_top"
    SCANNING_BOTTOM = "scanning_bottom"
    PURCHASING = "purchasing"
    REFRESHING = "refreshing"
    STOPPED = "stopped"


class OverlayActivityStatus(str, Enum):
    STARTED = "已启动"
    REFRESHING = "刷新ing..."
    TRANSFERRING = "转运ing..."
    RECONNECTING = "重连中"
    STOPPED = "已停止"


class StopReason(str, Enum):
    BUDGET_COMPLETE = "budget_complete"
    REFRESH_STRATEGY_EXHAUSTED = "refresh_strategy_exhausted"
    MANUAL_F5 = "manual_f5"
    PURCHASE_FUNDS_INSUFFICIENT = "purchase_funds_insufficient"
    WINDOW_ABNORMAL = "window_abnormal"
    INVALID_DISPLAY_GEOMETRY = "invalid_display_geometry"
    UNSUPPORTED_DISPLAY_RESOLUTION = "unsupported_display_resolution"
    DISPLAY_CHANGED = "display_changed"
    PERMISSION_REQUIRED = "permission_required"
    INPUT_FAILURE = "input_failure"
    HOTKEY_FAILURE = "hotkey_failure"
    RECOGNITION_TIMEOUT = "recognition_timeout"
    REFRESH_CLICK_UNACKNOWLEDGED = "refresh_click_unacknowledged"
    PURCHASE_RESULT_AMBIGUOUS = "purchase_result_ambiguous"
    REFRESH_BALANCE_MISMATCH = "refresh_balance_mismatch"
    SCROLL_VERIFICATION_FAILED = "scroll_verification_failed"
    CONFIG_INCOMPLETE = "config_incomplete"
    OVERLAY_CAPTURE_UNSAFE = "overlay_capture_unsafe"
    ENTRY_FAILURE = "entry_failure"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class TargetTally:
    target_id: str
    display_name: str
    acquired: int = 0

    def incremented(self) -> "TargetTally":
        return replace(self, acquired=self.acquired + 1)


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    run_id: str
    state: RunState
    targets: tuple[TargetTally, ...]
    refresh_spent: int
    refresh_limit: int
    refreshes_without_mandatory_target: int = 0
    overlay_status: OverlayActivityStatus = OverlayActivityStatus.STARTED
    is_final: bool = False
    stop_reason: StopReason | None = None

    @classmethod
    def initial(
        cls,
        run_id: str,
        targets: tuple[tuple[str, str], ...],
        refresh_limit: int,
    ) -> "RuntimeSnapshot":
        return cls(
            run_id=run_id,
            state=RunState.PREPARING,
            targets=tuple(TargetTally(target_id, name) for target_id, name in targets),
            refresh_spent=0,
            refresh_limit=refresh_limit,
        )

    def transitioned(self, state: RunState) -> "RuntimeSnapshot":
        if self.is_final:
            return self
        return replace(self, state=state)

    def with_refresh_spent(self, amount: int) -> "RuntimeSnapshot":
        if self.is_final:
            return self
        return replace(self, refresh_spent=amount)

    def with_refreshes_without_mandatory_target(
        self,
        count: int,
    ) -> "RuntimeSnapshot":
        if self.is_final:
            return self
        if count < 0:
            raise ValueError("refreshes_without_mandatory_target must be non-negative")
        return replace(self, refreshes_without_mandatory_target=count)

    def with_overlay_status(
        self,
        status: OverlayActivityStatus,
    ) -> "RuntimeSnapshot":
        if self.is_final:
            return self
        return replace(self, overlay_status=status)

    def with_incremented_target(self, target_id: str) -> "RuntimeSnapshot":
        if self.is_final:
            return self
        found = False
        updated: list[TargetTally] = []
        for tally in self.targets:
            if tally.target_id == target_id:
                found = True
                updated.append(tally.incremented())
            else:
                updated.append(tally)
        if not found:
            raise KeyError(f"Unknown target: {target_id}")
        return replace(self, targets=tuple(updated))

    def finalized(self, reason: StopReason) -> "RuntimeSnapshot":
        if self.is_final:
            return self
        return replace(
            self,
            state=RunState.STOPPED,
            overlay_status=OverlayActivityStatus.STOPPED,
            is_final=True,
            stop_reason=reason,
        )
