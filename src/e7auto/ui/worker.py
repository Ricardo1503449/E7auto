from __future__ import annotations

from pathlib import Path
import uuid
import traceback
import time

from PySide6.QtCore import QObject, Signal, Slot

from e7auto.bootstrap import create_production_session
from e7auto.configuration.models import AppConfig
from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.logging.run import RunLogManager
from e7auto.runtime.stop_control import StopController, StopExecution
from e7auto.ui.overlay import StatsOverlay
from e7auto.features.shop.reporting import ShopReportRecorder


class AutomationWorker(QObject):
    snapshot = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        config: AppConfig,
        refresh_limit: int,
        buy_friendship_points: bool,
        project_root: Path,
        overlay: StatsOverlay,
        *,
        purchase_limit: int | None = None,
        round_limit: int | None = None,
        continuous_refresh: bool = False,
        report_recorder: ShopReportRecorder | None = None,
    ):
        super().__init__()
        self._config = config
        self._refresh_limit = refresh_limit
        self._buy_friendship_points = buy_friendship_points
        self._project_root = project_root
        self._overlay = overlay
        self._purchase_limit = purchase_limit
        if purchase_limit is not None and round_limit is not None:
            raise ValueError("only one feature limit may be selected")
        self._round_limit = round_limit
        self._continuous_refresh = continuous_refresh
        self._control = StopController()
        self._last_snapshot: RuntimeSnapshot | None = None
        self._report_recorder = report_recorder if purchase_limit is None and round_limit is None else None
        self._reporting_logger = None

    def request_stop(self) -> None:
        # Called directly from the GUI thread: run() occupies the worker event loop.
        # Only the shared stop lock is touched, including before session creation.
        self._control.request(StopReason.MANUAL_STOP)

    @Slot()
    def run(self) -> None:
        run_id = uuid.uuid4().hex[:12]
        logger = None
        final = self._initial_snapshot(run_id).finalized(StopReason.INTERNAL_ERROR)
        try:
            logger = RunLogManager(self._project_root / "logs", self._config.logging).start(run_id)
            self._reporting_logger = logger
            session = create_production_session(
                self._config, "chaos" if self._round_limit is not None else "shop" if self._purchase_limit is None else "penguin",
                self._overlay, logger, self._publish_snapshot, self._control,
            )
            enabled_optional = (
                frozenset({"friendship_points"})
                if self._buy_friendship_points
                else frozenset()
            )
            if self._round_limit is not None:
                final = session.run_chaos(self._round_limit, run_id)
            elif self._purchase_limit is None:
                final = session.run(
                    self._refresh_limit, run_id,
                    enabled_optional_target_ids=enabled_optional,
                    continuous_refresh=self._continuous_refresh,
                )
            else:
                final = session.run_penguins(self._purchase_limit, run_id)
        except StopExecution as exc:
            final = (self._last_snapshot or self._initial_snapshot(run_id)).finalized(exc.reason)
            self._publish_snapshot(final)
        except Exception as exc:
            self.failed.emit(f"启动或运行失败：{type(exc).__name__}: {exc}")
            if logger is not None:
                self._record_failure(logger, exc)
            previous = self._last_snapshot or self._initial_snapshot(run_id)
            final = previous if previous.is_final else previous.finalized(StopReason.INTERNAL_ERROR)
            if final is not self._last_snapshot:
                self._publish_snapshot(final)
        finally:
            if self._report_recorder is not None:
                self._report_recorder.observe(final, logger)
            if logger is not None:
                try:
                    logger.close()
                except Exception as exc:
                    self.failed.emit(f"日志关闭失败：{type(exc).__name__}: {exc}")
            # Diagnostic failures cannot suppress the terminal signal.
            self.finished.emit(final)

    def _publish_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        self._last_snapshot = snapshot
        if self._report_recorder is not None:
            self._report_recorder.observe(snapshot, self._reporting_logger)
        self.snapshot.emit(snapshot)

    def _initial_snapshot(self, run_id: str) -> RuntimeSnapshot:
        if self._round_limit is not None:
            return RuntimeSnapshot.chaos_run(run_id, self._round_limit)
        if self._purchase_limit is not None:
            return RuntimeSnapshot.penguins(run_id, self._purchase_limit)
        return RuntimeSnapshot.initial(
            run_id, tuple((target.target_id, target.display_name) for target in self._config.targets),
            self._refresh_limit,
        )

    @staticmethod
    def _record_failure(logger, error: Exception) -> None:
        try:
            logger.event("worker_setup_failed", error=repr(error), traceback=traceback.format_exc())
        except Exception:
            pass
        try:
            logger.save_stop_snapshot(
                None, stop_reason=StopReason.INTERNAL_ERROR.value, stopped_monotonic=time.monotonic(),
            )
        except Exception as snapshot_error:
            try:
                logger.event(
                    "stop_snapshot", outcome="failed", stop_reason=StopReason.INTERNAL_ERROR.value,
                    source="last_successful_capture", detail=f"snapshot writer failed: {snapshot_error!r}",
                )
            except Exception:
                pass
