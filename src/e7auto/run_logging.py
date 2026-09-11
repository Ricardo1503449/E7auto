from __future__ import annotations

import logging
import msvcrt
import platform
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import BinaryIO, Callable

from . import __version__
from .config import LoggingConfig


_RUN_LOG_NAME = re.compile(r"^(run-.*\.log)(?:\.\d+)?$")
_CONTEXT_EVENTS = {"run_log_started", "window_prepared", "wgc_initialized"}


def _safe_value(value: object) -> str:
    return str(value).replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def _message(event: str, fields: dict[str, object]) -> str:
    suffix = " ".join(f"{key}={_safe_value(value)}" for key, value in sorted(fields.items()))
    return f"event={event}" + (f" {suffix}" if suffix else "")


def _acquire_lease(path: Path) -> BinaryIO:
    stream = path.open("a+b")
    try:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
    except BaseException:
        stream.close()
        raise
    return stream


def _release_lease(stream: BinaryIO) -> None:
    stream.seek(0)
    try:
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        stream.close()


class _RunFileHandler(RotatingFileHandler):
    def __init__(self, path: Path, config: LoggingConfig) -> None:
        super().__init__(path, maxBytes=config.max_file_mb * 1024 * 1024,
                         backupCount=config.backup_count, encoding="utf-8")
        self.context: dict[str, logging.LogRecord] = {}
        self.after_rollover: Callable[[], None] = lambda: None

    def write_direct(self, level: int, event: str, **fields: object) -> None:
        record = logging.LogRecord("e7auto.log", level, "", 0, _message(event, fields), (), None)
        logging.FileHandler.emit(self, record)

    def doRollover(self) -> None:
        super().doRollover()
        self.write_direct(logging.INFO, "log_segment_started", context_repeated=True)
        for record in self.context.values():
            logging.FileHandler.emit(self, record)
        self.after_rollover()


@dataclass(slots=True)
class RunLogger:
    logger: logging.Logger
    path: Path
    handler: _RunFileHandler
    on_close: Callable[[], None] = field(default=lambda: None)
    _closed: bool = False

    def event(self, event: str, **fields: object) -> None:
        if self._closed:
            return
        level = logging.WARNING if event == "log_cleanup_warning" else logging.INFO
        record = self.logger.makeRecord(self.logger.name, level, "", 0, _message(event, fields), (), None)
        if event in _CONTEXT_EVENTS:
            self.handler.context[event] = record
        self.logger.handle(record)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            # Cleanup warnings are written while this run still owns its file/lease.
            self.handler.after_rollover()
        finally:
            try:
                self.handler.flush()
                self.handler.close()
                self.logger.removeHandler(self.handler)
            finally:
                self.on_close()


class RunLogManager:
    def __init__(self, directory: Path, config: LoggingConfig):
        self._directory = directory
        self._config = config

    def start(self, run_id: str) -> RunLogger:
        self._directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
        path = self._directory / f"run-{timestamp}-{run_id}.log"
        lease_path = path.with_name(path.name + ".lock")
        lease = _acquire_lease(lease_path)
        try:
            handler = _RunFileHandler(path, self._config)
        except BaseException:
            _release_lease(lease)
            lease_path.unlink(missing_ok=True)
            raise
        logger = logging.getLogger(f"e7auto.run.{run_id}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
        logger.addHandler(handler)

        def cleanup() -> None:
            try:
                warnings = self._prune(protected=path.name)
            except OSError as exc:
                warnings = [f"cleanup failed: {exc}"]
            for warning in warnings:
                handler.write_direct(logging.WARNING, "log_cleanup_warning", detail=warning)

        def release() -> None:
            try:
                _release_lease(lease)
            except OSError:
                # Closing the descriptor releases the OS lock even if explicit unlock fails.
                pass
            try:
                lease_path.unlink(missing_ok=True)
            except OSError:
                # A stale unlocked lease is harmless and removed with its run later.
                pass

        handler.after_rollover = cleanup
        result = RunLogger(logger, path, handler, release)
        result.event("run_log_started", path=path.name, run_id=run_id,
                     version=__version__, windows=platform.platform(),
                     **asdict(self._config))
        cleanup()
        return result

    def _prune(self, *, protected: str) -> list[str]:
        warnings: list[str] = []
        groups: dict[str, list[tuple[Path, float, int]]] = {}
        try:
            for path in self._directory.iterdir():
                match = _RUN_LOG_NAME.fullmatch(path.name)
                if match is None or path.is_symlink() or not path.is_file():
                    continue
                try:
                    stat = path.stat()
                    groups.setdefault(match[1], []).append((path, stat.st_mtime, stat.st_size))
                except OSError as exc:
                    warnings.append(f"cannot inspect {path.name}: {exc}")
        except OSError as exc:
            return [f"cannot scan logs: {exc}"]
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self._config.keep_days)).timestamp()
        total = sum(size for group in groups.values() for _, _, size in group)
        count = len(groups)
        for name, group in sorted(groups.items(), key=lambda item: max(row[1] for row in item[1])):
            if name == protected:
                continue
            expired = max(row[1] for row in group) < cutoff
            if not (expired or count > self._config.keep_runs or total > self._config.max_total_mb * 1024 * 1024):
                continue
            lease_path = self._directory / (name + ".lock")
            try:
                lease = _acquire_lease(lease_path)
            except OSError as exc:
                warnings.append(f"skip active or inaccessible run {name}: {exc}")
                continue
            removed = True
            try:
                for path, _, size in group:
                    try:
                        path.unlink(missing_ok=True)
                        total -= size
                    except OSError as exc:
                        removed = False
                        warnings.append(f"cannot remove {path.name}: {exc}")
                if removed:
                    count -= 1
            finally:
                _release_lease(lease)
                try:
                    lease_path.unlink(missing_ok=True)
                except OSError as exc:
                    warnings.append(f"cannot remove lease {lease_path.name}: {exc}")
        return warnings
