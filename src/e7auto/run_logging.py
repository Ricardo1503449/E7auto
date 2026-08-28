from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import LoggingConfig


def _safe_value(value: object) -> str:
    return str(value).replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


@dataclass(slots=True)
class RunLogger:
    logger: logging.Logger
    path: Path
    handler: logging.Handler
    profile: str = "detailed"

    def event(self, event: str, **fields: object) -> None:
        if self.profile == "compact" and not self._should_write_compact(event, fields):
            return
        suffix = " ".join(f"{key}={_safe_value(value)}" for key, value in sorted(fields.items()))
        self.logger.info("event=%s%s", event, f" {suffix}" if suffix else "")

    @staticmethod
    def _should_write_compact(event: str, fields: dict[str, object]) -> bool:
        """Keep only user-actionable outcomes in the end-user build.

        Detailed diagnostics remain available through the source profile.  The
        compact profile deliberately omits all polling, timing, coordinates,
        confidence values, and state-machine progress; the final ``run_stopped``
        record carries the user-visible result/reason.
        """
        return event in {
            "run_log_started",
            "run_stopped",
            "startup_rejected",
            "worker_setup_failed",
            "input_failed",
            "network_error_detected",
            "network_recovered",
            "purchase_counted",
            "refresh_counted",
            "refresh_strategy_exhausted",
            "refresh_click_unacknowledged",
        }

    def close(self) -> None:
        self.handler.flush()
        self.handler.close()
        self.logger.removeHandler(self.handler)


class RunLogManager:
    def __init__(self, directory: Path, config: LoggingConfig):
        self._directory = directory
        self._config = config

    def start(self, run_id: str) -> RunLogger:
        self._directory.mkdir(parents=True, exist_ok=True)
        self._prune()
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        path = self._directory / f"run-{timestamp}-{run_id}.log"
        logger = logging.getLogger(f"e7auto.run.{run_id}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        max_bytes = max(0, int(getattr(self._config, "max_file_mb", 0))) * 1024 * 1024
        handler_type = RotatingFileHandler if max_bytes else logging.FileHandler
        handler = handler_type(
            path,
            encoding="utf-8",
            delay=False,
            **({"maxBytes": max_bytes, "backupCount": 2} if max_bytes else {}),
        )
        handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
        logger.addHandler(handler)
        result = RunLogger(logger, path, handler, self._config.profile)
        result.event("run_log_started", path=path.name)
        return result

    def _prune(self) -> None:
        files = sorted(
            (path for path in self._directory.glob("run-*.log") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._config.keep_days)
        retained_before_new = max(self._config.keep_files - 1, 0)
        for index, path in enumerate(files):
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if index >= retained_before_new or modified < cutoff:
                path.unlink(missing_ok=True)
