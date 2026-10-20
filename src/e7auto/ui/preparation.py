"""Cancellable resource validation; never creates game services."""
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Literal
import uuid

from PySide6.QtCore import QThread

from e7auto.configuration.loader import load_config
from e7auto.configuration.models import AppConfig, ConfigError, LoggingConfig
from e7auto.core.domain import StopReason
from e7auto.features.chaos.configuration import with_chaos_config
from e7auto.features.penguin.configuration import with_penguin_config
from e7auto.logging.run import RunLogManager


@dataclass(frozen=True, slots=True)
class RunRequest:
    feature: Literal["shop", "penguin", "chaos"]
    limit: int
    buy_friendship_points: bool = False
    continuous_refresh: bool = False
    target_stage: int = 1
    lineup_id: str = "dark_twins"


class PreparationThread(QThread):
    def __init__(self, config_path: Path, request: RunRequest, parent=None):
        super().__init__(parent)
        self._config_path = config_path
        self.request = request
        self.cancelled = Event()
        self.config: AppConfig | None = None
        self.error = ""

    def cancel(self) -> None:
        self.cancelled.set()

    def run(self) -> None:
        try:
            if self.cancelled.is_set():
                return
            config = load_config(self._config_path, template_profile=self.request.feature)
            if self.cancelled.is_set():
                return
            if self.request.feature == "chaos":
                config = with_chaos_config(config, target_stage=self.request.target_stage,
                                           lineup_id=self.request.lineup_id)
            elif self.request.feature == "penguin":
                config = with_penguin_config(config)
            if not self.cancelled.is_set():
                self.config = config
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, ConfigError) and not self.cancelled.is_set():
                self._record_rejection(exc)

    def _record_rejection(self, error: ConfigError) -> None:
        logger = None
        try:
            logger = RunLogManager(self._config_path.parent.parent / "logs", LoggingConfig()).start(uuid.uuid4().hex[:12])
            logger.event("startup_rejected", reason=StopReason.CONFIG_INCOMPLETE.value,
                         errors=" | ".join(error.errors))
        except Exception as exc:
            self.error += f"\n无法写入启动日志：{exc}"
        finally:
            if logger is not None:
                try:
                    logger.close()
                except Exception as exc:
                    self.error += f"\n无法关闭启动日志：{exc}"
