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
from e7auto.ui.overlay import StatsOverlay


class AutomationWorker(QObject):
    snapshot = Signal(object)
    finished = Signal(object)

    def __init__(
        self,
        config: AppConfig,
        refresh_limit: int,
        buy_friendship_points: bool,
        project_root: Path,
        overlay: StatsOverlay,
        *,
        purchase_limit: int | None = None,
    ):
        super().__init__()
        self._config = config
        self._refresh_limit = refresh_limit
        self._buy_friendship_points = buy_friendship_points
        self._project_root = project_root
        self._overlay = overlay
        self._purchase_limit = purchase_limit

    @Slot()
    def run(self) -> None:
        run_id = uuid.uuid4().hex[:12]
        logger = RunLogManager(self._project_root / "logs", self._config.logging).start(run_id)
        try:
            session = create_production_session(
                self._config, "shop" if self._purchase_limit is None else "penguin",
                self._overlay, logger, self.snapshot.emit,
            )
            enabled_optional = (
                frozenset({"friendship_points"})
                if self._buy_friendship_points
                else frozenset()
            )
            if self._purchase_limit is None:
                final = session.run(
                    self._refresh_limit, run_id,
                    enabled_optional_target_ids=enabled_optional,
                )
            else:
                final = session.run_penguins(self._purchase_limit, run_id)
        except Exception as exc:
            logger.event("worker_setup_failed", error=repr(exc), traceback=traceback.format_exc())
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
            finally:
                logger.close()
            initial = RuntimeSnapshot.initial(
                run_id,
                tuple((target.target_id, target.display_name) for target in self._config.targets),
                self._refresh_limit,
            )
            if self._purchase_limit is not None:
                initial = RuntimeSnapshot.penguins(run_id, self._purchase_limit)
            final = initial.finalized(StopReason.INTERNAL_ERROR)
            self.snapshot.emit(final)
        self.finished.emit(final)
