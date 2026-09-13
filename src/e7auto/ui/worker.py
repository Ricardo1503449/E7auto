from __future__ import annotations

from pathlib import Path
import uuid

from PySide6.QtCore import QObject, Signal, Slot

from ..automation import AutomationDependencies, AutomationSession, SystemClock
from ..background_windows import Win32WindowMessageInputService
from ..config import AppConfig
from ..domain import RuntimeSnapshot, StopReason
from ..platform_windows import Win32F5HotkeyService, Win32RuntimeEnvironment, Win32WindowService
from ..run_logging import RunLogManager
from ..vision import OpenCvGameVision, TemplateRepository
from .overlay import StatsOverlay


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
            # Load PyWinRT/WGC only inside the automation worker.  Keeping the
            # native WGC modules out of the Qt startup path avoids mixing their
            # COM lifetime with the UI thread and makes shutdown deterministic.
            from ..wgc_capture import WindowsGraphicsCaptureService

            templates = TemplateRepository(self._config)
            if self._purchase_limit is None:
                vision = OpenCvGameVision(self._config, templates)
            else:
                from ..penguin_vision import PenguinVision
                vision = PenguinVision(self._config, templates)
            dependencies = AutomationDependencies(
                windows=Win32WindowService(),
                capture=WindowsGraphicsCaptureService(),
                inputs=Win32WindowMessageInputService(),
                overlay=self._overlay,
                vision=vision,
                clock=SystemClock(),
                logger=logger,
                runtime=Win32RuntimeEnvironment(),
            )
            session = AutomationSession(
                self._config,
                dependencies,
                Win32F5HotkeyService(),
                self.snapshot.emit,
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
            logger.event("worker_setup_failed", error=repr(exc))
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
