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
from .ports import CachedGameFrame


_RUN_LOG_NAME = re.compile(r"^(run-.*\.log)(?:\.\d+)?$")
_STOP_SNAPSHOT_NAME = re.compile(r"^(run-[0-9]{8}-[0-9]{6}-[0-9]{6}-.+)-stop\.png(?:\.tmp)?$")
_CONTEXT_EVENTS = {"run_log_started", "window_prepared", "wgc_initialized"}
_DECIMAL_LOG_FIELDS = frozenset({
    "confidence", "max_confidence", "minimum_phase_response", "shift_tolerance_px",
    "duration_ms", "capture_ms", "vision_ms", "normalization_ms", "cache_wait_ms",
    "scale_x", "scale_y", "phase_shift_x", "phase_shift_y", "phase_response",
    "mean_absolute_difference", "changed_fraction", "total_phase_shift_y",
    "total_phase_response", "total_changed_fraction", "cumulative_shift_x",
    "cumulative_shift_y", "fallback_overlap_height_fraction", "fallback_block_scores",
    "pair_shift_y", "pair_response", "pair_changed_fraction",
})
_PLAIN_DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)\.[0-9]+")


def _safe_value(value: object) -> str:
    return str(value).replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def _message(event: str, fields: dict[str, object]) -> str:
    suffix = " ".join(f"{key}={_field_value(key, value)}" for key, value in sorted(fields.items()))
    return f"event={event}" + (f" {suffix}" if suffix else "")


def _field_value(key: str, value: object) -> str:
    """Trim insignificant zeros only in known measurements, never arbitrary text.

    No float conversion/rounding: decimal values, signs, and vector positions survive.
    """
    text = _safe_value(value)
    if key not in _DECIMAL_LOG_FIELDS:
        return text
    return ",".join(
        item.rstrip("0").rstrip(".")
        if item.endswith("0") and _PLAIN_DECIMAL.fullmatch(item) else item
        for item in text.split(",")
    )


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


def _run_group_name(name: str) -> str | None:
    log = _RUN_LOG_NAME.fullmatch(name)
    if log is not None:
        return log[1]
    snapshot = _STOP_SNAPSHOT_NAME.fullmatch(name)
    return snapshot[1] + ".log" if snapshot is not None else None


def _is_local_path(path: Path, directory: Path) -> bool:
    return not path.is_symlink() and path.resolve().parent == directory.resolve()


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
    _stop_snapshot_attempted: bool = False

    def event(self, event: str, **fields: object) -> None:
        if self._closed:
            return
        level = logging.WARNING if event == "log_cleanup_warning" else logging.INFO
        record = self.logger.makeRecord(self.logger.name, level, "", 0, _message(event, fields), (), None)
        if event in _CONTEXT_EVENTS:
            self.handler.context[event] = record
        self.logger.handle(record)

    def save_stop_snapshot(
        self, cached: CachedGameFrame | None, *, stop_reason: str, stopped_monotonic: float,
    ) -> None:
        if self._closed or self._stop_snapshot_attempted:
            return
        self._stop_snapshot_attempted = True
        fields: dict[str, object] = {
            "stop_reason": stop_reason, "source": "last_successful_capture",
        }
        if cached is None:
            self.event("stop_snapshot", outcome="skipped", detail="no_cached_frame", **fields)
            return

        temporary: Path | None = None
        owns_temporary = False
        try:
            # Import/convert/encode only on the abnormal stop path.
            import cv2
            import numpy as np

            frame = cached.frame
            fields.update(
                captured_at=cached.captured_at,
                frame_age_ms=max(0, round((stopped_monotonic - cached.captured_monotonic) * 1000)),
            )
            if frame.dtype != np.uint8 or frame.ndim != 3 or frame.shape[2] not in (3, 4):
                raise ValueError("cached game frame must be an HxWx3 or HxWx4 uint8 image")
            height, width = frame.shape[:2]
            if height == 0 or width == 0:
                raise ValueError("cached game frame is empty")
            fields.update(width=width, height=height)
            target = self.path.with_name(self.path.stem + "-stop.png")
            temporary = target.with_name(target.name + ".tmp")
            for candidate in (target, temporary):
                if not _is_local_path(candidate, self.path.parent):
                    raise ValueError("snapshot path must stay inside the run log directory")
                if candidate.exists():
                    raise FileExistsError(f"snapshot file already exists: {candidate.name}")
            image = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR) if frame.shape[2] == 4 else frame
            success, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
            if not success:
                raise OSError("PNG encoding failed")
            data = encoded.tobytes()
            with temporary.open("xb") as stream:
                owns_temporary = True
                if stream.write(data) != len(data):
                    raise OSError("incomplete PNG write")
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"snapshot file already exists: {target.name}")
            # On Windows rename refuses to overwrite an existing destination.
            temporary.rename(target)
            owns_temporary = False
            fields.update(outcome="saved", path=target.name)
        except Exception as exc:
            fields.update(outcome="failed", detail=repr(exc))
        finally:
            if owns_temporary and temporary is not None:
                try:
                    if not _is_local_path(temporary, self.path.parent):
                        raise ValueError("temporary snapshot path changed")
                    temporary.unlink(missing_ok=True)
                except Exception as exc:
                    fields["detail"] = f"{fields.get('detail', '')}; temporary cleanup failed: {exc!r}"
        self.event("stop_snapshot", **fields)

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
                try:
                    name = _run_group_name(path.name)
                    if name is None or not _is_local_path(path, self._directory) or not path.is_file():
                        continue
                    stat = path.stat()
                    groups.setdefault(name, []).append((path, stat.st_mtime, stat.st_size))
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
                if not _is_local_path(lease_path, self._directory):
                    raise OSError("lease path is outside the log directory or is a symlink")
                lease = _acquire_lease(lease_path)
            except OSError as exc:
                warnings.append(f"skip active or inaccessible run {name}: {exc}")
                continue
            removed = True
            try:
                for path, _, size in group:
                    try:
                        if not _is_local_path(path, self._directory):
                            raise OSError("log artifact path changed or is a symlink")
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
                    if not _is_local_path(lease_path, self._directory):
                        raise OSError("lease path changed or is a symlink")
                    lease_path.unlink(missing_ok=True)
                except OSError as exc:
                    warnings.append(f"cannot remove lease {lease_path.name}: {exc}")
        return warnings
