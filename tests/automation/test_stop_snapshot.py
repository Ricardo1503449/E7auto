from __future__ import annotations

from datetime import datetime

import cv2
import numpy as np
import pytest

from e7auto.bootstrap import AutomationSession
from e7auto.runtime.snapshots import SnapshotPublisher
from e7auto.runtime.stop_control import StopController
from e7auto.features.shop.flow import ShopFlow as AutomationEngine
from e7auto.features.penguin.flow import PenguinFlow as PenguinEngine
from e7auto.runtime.stop_control import StopExecution
from e7auto.configuration.models import LoggingConfig
from e7auto.core.domain import RuntimeSnapshot, StopReason
from e7auto.core.ports import CaptureError
from e7auto.logging.run import RunLogManager
from tests.helpers import FakeCapture, FakeClock, FakeHotkeys, FakeLogger, ScriptedVision, make_config, make_dependencies


class Capture(FakeCapture):
    def __init__(self):
        super().__init__()
        self.last = None
        self.fail = False

    def capture_client(self, window, bounds):
        if self.fail:
            self.calls += 1
            raise CaptureError("synthetic capture failure")
        self.last = super().capture_client(window, bounds)
        return self.last


def dependencies(logger=None):
    clock = FakeClock()
    deps, *_ = make_dependencies(ScriptedVision(), logger=logger, clock=clock)
    deps.capture = Capture()
    return deps, clock


def test_cache_is_latest_raw_reference_survives_capture_failure_and_can_be_released():
    deps, clock = dependencies()
    initial = RuntimeSnapshot.initial("cache", (), 0)
    engine = AutomationEngine(make_config(), deps, StopController(), SnapshotPublisher(initial, lambda _: None), frozenset())
    engine.runtime.prepare()
    assert engine.cached_game_frame() is None
    engine.runtime.capture_raw()
    first = engine.cached_game_frame()
    assert first.frame is deps.capture.last
    clock.sleep(2)
    engine.runtime.capture_raw()
    second = engine.cached_game_frame()
    assert second is not first
    assert second.frame is deps.capture.last
    assert second.captured_monotonic == clock.now
    assert datetime.fromisoformat(second.captured_at).tzinfo is not None
    deps.capture.fail = True
    with pytest.raises(StopExecution) as error:
        engine.runtime.capture_raw()
    assert error.value.reason is StopReason.CAPTURE_FAILURE
    assert engine.cached_game_frame() is second
    engine.release_cached_game_frame()
    assert engine.cached_game_frame() is None


@pytest.mark.parametrize("penguins", [False, True])
@pytest.mark.parametrize("failure", ["scroll", "recognition", "internal", "capture"])
def test_exception_saves_last_cache_once_before_closing_capture(tmp_path, monkeypatch, penguins, failure):
    log = RunLogManager(tmp_path, LoggingConfig()).start("stop")
    deps, clock = dependencies(log)
    engines = []
    expected = {"scroll": StopReason.SCROLL_VERIFICATION_FAILED, "recognition": StopReason.RECOGNITION_TIMEOUT,
                "internal": StopReason.INTERNAL_ERROR, "capture": StopReason.CAPTURE_FAILURE}[failure]

    def execute(engine):
        engines.append(engine)
        assert engine.cached_game_frame() is None
        engine.runtime.prepare()
        engine.runtime.capture_raw()
        engine.runtime.capture_raw()
        clock.sleep(2.5)
        engine.runtime.network_paused_seconds = 2.0  # Frame age must include the network wait.
        if failure == "internal": raise ValueError("original internal failure")
        if failure == "capture":
            deps.capture.fail = True
            engine.runtime.capture_raw()
        raise StopExecution(expected, "original diagnostic detail")

    engine_type = PenguinEngine if penguins else AutomationEngine
    monkeypatch.setattr(engine_type, "execute", execute)
    original_save = type(log).save_stop_snapshot

    def save_before_close(logger, *args, **kwargs):
        assert not deps.capture.closed
        return original_save(logger, *args, **kwargs)

    monkeypatch.setattr(type(log), "save_stop_snapshot", save_before_close)
    hotkeys = FakeHotkeys()
    session = AutomationSession(make_config(), deps, hotkeys, lambda _: None)
    final = session.run_penguins(1) if penguins else session.run(0)
    assert final.stop_reason is expected
    assert deps.capture.calls == (3 if failure == "capture" else 2)
    assert deps.capture.closed and hotkeys.unregistered == 1
    assert engines[0].cached_game_frame() is None
    files = list(tmp_path.glob("*-stop.png"))
    assert len(files) == 1
    image = cv2.imdecode(np.frombuffer(files[0].read_bytes(), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    np.testing.assert_array_equal(image, deps.capture.last)
    text = log.path.read_text(encoding="utf-8")
    assert text.count("event=stop_snapshot ") == 1
    assert "frame_age_ms=2500" in text and "outcome=saved" in text
    assert f"reason={expected.value}" in text.splitlines()[-1]
    if failure == "internal": assert "traceback=" in text and "original internal failure" in text


@pytest.mark.parametrize("reason", [StopReason.BUDGET_COMPLETE, StopReason.REFRESH_STRATEGY_EXHAUSTED,
    StopReason.PENGUIN_LIMIT_COMPLETE, StopReason.PENGUIN_FUNDS_COMPLETE,
    StopReason.PURCHASE_FUNDS_INSUFFICIENT, StopReason.MANUAL_F5])
def test_expected_stops_never_encode_or_save(tmp_path, monkeypatch, reason):
    log = RunLogManager(tmp_path, LoggingConfig()).start("normal")
    deps, _ = dependencies(log)
    engines = []

    def execute(engine):
        engines.append(engine)
        engine.runtime.prepare()
        engine.runtime.capture_raw()
        raise StopExecution(reason)

    monkeypatch.setattr(AutomationEngine, "execute", execute)
    monkeypatch.setattr(AutomationEngine, "finish_normal_run", lambda *_: None)
    monkeypatch.setattr(cv2, "imencode", lambda *_: pytest.fail("Normal stop must not encode"))
    final = AutomationSession(make_config(), deps, FakeHotkeys(), lambda _: None).run(0)
    assert final.stop_reason is reason
    assert deps.capture.calls == 1
    assert engines[0].cached_game_frame() is None
    assert not list(tmp_path.glob("*.png*"))
    assert "stop_snapshot" not in log.path.read_text(encoding="utf-8")


def test_failure_in_normal_return_home_saves_final_cached_frame(tmp_path, monkeypatch):
    log = RunLogManager(tmp_path, LoggingConfig()).start("exit")
    deps, _ = dependencies(log)

    def execute(engine):
        engine.runtime.prepare()
        engine.runtime.capture_raw()
        raise StopExecution(StopReason.BUDGET_COMPLETE)

    def finish(engine, reason):
        engine.runtime.capture_raw()
        raise StopExecution(StopReason.ENTRY_FAILURE, "exit recognition failed")

    monkeypatch.setattr(AutomationEngine, "execute", execute)
    monkeypatch.setattr(AutomationEngine, "finish_normal_run", finish)
    final = AutomationSession(make_config(), deps, FakeHotkeys(), lambda _: None).run(0)
    assert final.stop_reason is StopReason.ENTRY_FAILURE
    image = cv2.imdecode(np.frombuffer(next(tmp_path.glob("*.png")).read_bytes(), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    np.testing.assert_array_equal(image, deps.capture.last)
    assert deps.capture.calls == 2


def test_no_engine_initialization_failure_records_no_cache(tmp_path):
    log = RunLogManager(tmp_path, LoggingConfig()).start("startup")
    deps, _ = dependencies(log)
    final = AutomationSession(make_config(), deps, FakeHotkeys(succeeds=False), lambda _: None).run(0)
    assert final.stop_reason is StopReason.HOTKEY_FAILURE
    assert deps.capture.calls == 0 and deps.capture.closed
    text = log.path.read_text(encoding="utf-8")
    assert "outcome=skipped" in text and "detail=no_cached_frame" in text
    assert "event=run_stopped" in text.splitlines()[-1]


def test_writer_exception_does_not_replace_original_error_or_prevent_cleanup(monkeypatch):
    class BrokenLogger(FakeLogger):
        def save_stop_snapshot(self, *args, **kwargs): raise OSError("writer failed")

    logger = BrokenLogger()
    deps, _ = dependencies(logger)

    def execute(engine):
        engine.runtime.prepare()
        engine.runtime.capture_raw()
        raise StopExecution(StopReason.INPUT_FAILURE, "original input error")

    monkeypatch.setattr(AutomationEngine, "execute", execute)
    hotkeys = FakeHotkeys()
    final = AutomationSession(make_config(), deps, hotkeys, lambda _: None).run(0)
    assert final.stop_reason is StopReason.INPUT_FAILURE
    assert deps.capture.closed and hotkeys.unregistered == 1 and logger.closed == 1
    assert logger.events[-1][0] == "run_stopped"
    assert logger.events[-1][1]["detail"] == "original input error"


def test_second_run_cannot_reuse_first_runs_cache(tmp_path, monkeypatch):
    runs = []
    for index in range(2):
        log = RunLogManager(tmp_path, LoggingConfig()).start(f"run{index}")
        deps, _ = dependencies(log)

        def execute(engine):
            assert engine.cached_game_frame() is None
            if index == 0:
                engine.runtime.prepare()
                engine.runtime.capture_raw()
            raise StopExecution(StopReason.RECOGNITION_TIMEOUT)

        monkeypatch.setattr(AutomationEngine, "execute", execute)
        AutomationSession(make_config(), deps, FakeHotkeys(), lambda _: None).run(0)
        runs.append(log.path.read_text(encoding="utf-8"))
    assert "outcome=saved" in runs[0]
    assert "outcome=skipped" in runs[1]
    assert len(list(tmp_path.glob("*.png"))) == 1


def test_actual_png_encoding_failure_preserves_session_error_and_closes_resources(tmp_path, monkeypatch):
    log = RunLogManager(tmp_path, LoggingConfig()).start("codec")
    deps, _ = dependencies(log)

    def execute(engine):
        engine.runtime.prepare()
        engine.runtime.capture_raw()
        raise StopExecution(StopReason.REFRESH_BALANCE_MISMATCH, "original balance detail")

    def fail_encode(*_): raise OSError("synthetic codec failure")

    monkeypatch.setattr(AutomationEngine, "execute", execute)
    monkeypatch.setattr(cv2, "imencode", fail_encode)
    hotkeys = FakeHotkeys()
    final = AutomationSession(make_config(), deps, hotkeys, lambda _: None).run(0)
    assert final.stop_reason is StopReason.REFRESH_BALANCE_MISMATCH
    assert deps.capture.closed and hotkeys.unregistered == 1
    assert deps.capture.calls == 1
    text = log.path.read_text(encoding="utf-8")
    assert text.count("event=stop_snapshot ") == 1
    assert "outcome=failed" in text and "synthetic codec failure" in text
    assert "detail=original balance detail" in text.splitlines()[-1]
    assert "reason=refresh_balance_mismatch" in text.splitlines()[-1]
