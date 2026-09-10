from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from e7auto.config import Point, Rect
from e7auto.ports import WindowRef, WindowState
from scripts.validation import validate_background_mode
from tests.helpers import make_config
from tests.helpers.paths import ROOT


class FakeWindows:
    def __init__(self, state: WindowState) -> None:
        self.state = state

    def inspect(self, _window: WindowRef) -> WindowState:
        return self.state


def test_background_state_accepts_occlusion_but_rejects_minimize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bounds = Rect(10, 20, 100, 80)
    window = WindowRef(101, "game", "game.exe", r"D:\game.exe", 77)
    windows = FakeWindows(WindowState(True, False, False, bounds, bounds))
    monkeypatch.setattr(validate_background_mode.win32gui, "GetForegroundWindow", lambda: 202)

    validate_background_mode._check_background_state(windows, window, bounds, 202)

    windows.state = WindowState(True, True, False, bounds, bounds)
    with pytest.raises(RuntimeError, match="minimized"):
        validate_background_mode._check_background_state(windows, window, bounds, 202)


def test_capture_latency_reports_p95_and_maximum() -> None:
    samples = [
        {"timing_ms": {"capture": value}}
        for value in (10.0, 20.0, 30.0, 40.0)
    ]

    result = validate_background_mode._capture_latency(samples)

    assert result["minimum_ms"] == 10.0
    assert result["mean_ms"] == 25.0
    assert result["p95_ms"] == pytest.approx(38.5)
    assert result["maximum_ms"] == 40.0


def test_stability_summary_uses_consecutive_identical_samples() -> None:
    samples: list[dict[str, object]] = []
    inventory_keys = (
        (("top_1", "covenant_bookmark"),),
        (),
        (("top_1", "covenant_bookmark"),),
        (("top_1", "covenant_bookmark"),),
        (("top_1", "covenant_bookmark"),),
    )
    for index, inventory_key in enumerate(inventory_keys, start=1):
        samples.append(
            {
                "elapsed_ms": float(index * 100),
                "timing_ms": {
                    "capture": 1.0,
                    "refresh": 2.0,
                    "sky_stone": 3.0,
                    "inventory": 4.0,
                    "total": 10.0,
                },
                "refresh": {"detected": True, "confidence": 0.99},
                "sky_stone": {
                    "detected": True,
                    "value": 3825,
                    "confidence": 0.9,
                },
                "matches": [
                    {
                        "slot_id": slot,
                        "target_id": target,
                        "confidence": 0.98,
                    }
                    for slot, target in inventory_key
                ],
            }
        )

    summary = validate_background_mode.summarize_samples(samples, 3)

    assert summary["stability"]["refresh"]["sample"] == 3
    assert summary["stability"]["sky_stone"]["value"] == 3825
    assert summary["stability"]["inventory"]["sample"] == 5
    assert summary["observed_targets"] == ["covenant_bookmark"]
    assert summary["timing"]["total"]["mean_ms"] == 10.0


def test_maintained_capture_sources_have_no_mss_or_printwindow_backend() -> None:
    paths = [
        *(ROOT / "src" / "e7auto").rglob("*.py"),
        *(ROOT / "scripts").rglob("*.py"),
    ]
    forbidden = (
        "MssCaptureService",
        "import mss",
        "PrintWindow",
        "PrintWindowCaptureService",
        "PW_RENDERFULLCONTENT",
        "WM_PRINT",
    )
    violations = [
        f"{path.relative_to(ROOT)}: {symbol}"
        for path in sorted(paths)
        for symbol in forbidden
        if symbol in path.read_text(encoding="utf-8")
    ]

    assert violations == []


def test_extended_effect_observation_stops_on_first_visible_scroll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config()
    now = [0.0]
    monkeypatch.setattr(validate_background_mode.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        validate_background_mode.time,
        "sleep",
        lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    monkeypatch.setattr(validate_background_mode, "_check_background_state", lambda *args: None)
    monkeypatch.setattr(
        validate_background_mode,
        "_assert_cursor_and_foreground",
        lambda *args: None,
    )
    observations = iter(
        [
            SimpleNamespace(phase_shift_y=0.0, changed_fraction=0.0),
            SimpleNamespace(phase_shift_y=-10.0, changed_fraction=0.05),
            SimpleNamespace(
                phase_shift_y=-float(config.scroll.minimum_upward_shift_px + 1),
                changed_fraction=config.scroll.minimum_changed_fraction + 0.01,
            ),
        ]
    )
    monkeypatch.setattr(
        validate_background_mode,
        "measure_inventory_scroll",
        lambda *_args, **_kwargs: next(observations),
    )

    class FakeCapture:
        def capture_client(self, _window: object, _bounds: Rect) -> np.ndarray:
            return np.zeros((80, 100, 4), dtype=np.uint8)

    _, movement, trace = validate_background_mode._observe_scroll_effect(
        config=config,
        windows=object(),
        capture=FakeCapture(),
        window=object(),
        bounds=Rect(10, 20, 100, 80),
        foreground=202,
        cursor=(5, 6),
        before=np.zeros((80, 100, 4), dtype=np.uint8),
        observation_ms=10000,
    )

    assert len(trace) == 3
    assert trace[-1]["passed"] is True
    assert movement.phase_shift_y < -config.scroll.minimum_upward_shift_px


def test_scroll_validator_sends_only_calibrated_window_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config()
    bounds = Rect(10, 20, config.baseline_client_size.width, config.baseline_client_size.height)
    window = WindowRef(101, "game", "game.exe", r"D:\game.exe", 77)
    windows = FakeWindows(WindowState(True, False, False, bounds, bounds))
    vision = SimpleNamespace(
        main_shop_icon=lambda _frame: None,
        shop_ready=lambda _frame: None,
        shop_exit_icon=lambda _frame: None,
    )
    monkeypatch.setattr(
        validate_background_mode,
        "_base_context",
        lambda _args: (config, vision, windows, window, bounds, 202),
    )
    monkeypatch.setattr(validate_background_mode, "_check_background_state", lambda *args: None)
    monkeypatch.setattr(validate_background_mode, "_warm_capture", lambda **_kwargs: 12.0)
    monkeypatch.setattr(validate_background_mode.win32gui, "GetForegroundWindow", lambda: 202)
    monkeypatch.setattr(validate_background_mode.win32api, "GetCursorPos", lambda: (5, 6))
    monkeypatch.setattr(validate_background_mode.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        validate_background_mode,
        "_observe_scroll_effect",
        lambda **_kwargs: (
            frame,
            SimpleNamespace(
                phase_shift_x=0.0,
                phase_shift_y=-float(config.scroll.minimum_upward_shift_px + 1),
                phase_response=1.0,
                changed_fraction=config.scroll.minimum_changed_fraction + 0.01,
                mean_absolute_difference=10.0,
                maximum_difference=100,
            ),
            [{"elapsed_after_settle_ms": 1.0, "passed": True}],
        ),
    )

    sample = {
        "elapsed_ms": 1.0,
        "timing_ms": {
            "capture": 10.0,
            "refresh": 1.0,
            "sky_stone": 1.0,
            "inventory": 1.0,
            "total": 13.0,
        },
    }
    frame = np.zeros(
        (config.baseline_client_size.height, config.baseline_client_size.width, 4),
        dtype=np.uint8,
    )
    monkeypatch.setattr(
        validate_background_mode,
        "_sample_viewport",
        lambda **_kwargs: ([sample, sample, sample], frame, frame),
    )
    stable = {
        "stability": {
            "refresh": {"achieved": True},
            "sky_stone": {"achieved": True},
            "inventory": {"achieved": True},
        }
    }
    monkeypatch.setattr(validate_background_mode, "summarize_samples", lambda *_args: stable)
    monkeypatch.setattr(
        validate_background_mode,
        "measure_inventory_scroll",
        lambda *_args, **_kwargs: SimpleNamespace(
            phase_shift_x=0.0,
            phase_shift_y=-float(config.scroll.minimum_upward_shift_px + 1),
            phase_response=1.0,
            changed_fraction=config.scroll.minimum_changed_fraction + 0.01,
            mean_absolute_difference=10.0,
            maximum_difference=100,
        ),
    )

    class FakeCapture:
        def close(self) -> None:
            pass

    monkeypatch.setattr(validate_background_mode, "_capture_service", lambda _name: FakeCapture())
    actions: list[tuple[WindowRef, object, int]] = []

    class FakeInput:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def scroll(self, target: WindowRef, point: object, delta: int) -> None:
            actions.append((target, point, delta))

    monkeypatch.setattr(validate_background_mode, "Win32WindowMessageInputService", FakeInput)
    args = Namespace(
        sample_count=3,
        interval_ms=config.scroll.interval_ms,
        capture_backend="wgc",
        effect_observation_ms=config.scroll.settle_ms,
    )

    result, exit_code = validate_background_mode._run_scroll(args)

    assert exit_code == 0
    assert result["status"] == "ok"
    assert len(actions) == config.scroll.repetitions
    assert all(action == (window, config.scroll.cursor_point, config.scroll.delta) for action in actions)


def test_wgc_scroll_observation_starts_without_settle_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config()
    bounds = Rect(10, 20, config.baseline_client_size.width, config.baseline_client_size.height)
    window = WindowRef(101, "game", "game.exe", r"D:\game.exe", 77)
    windows = FakeWindows(WindowState(True, False, False, bounds, bounds))
    vision = SimpleNamespace()
    monkeypatch.setattr(
        validate_background_mode,
        "_base_context",
        lambda _args: (config, vision, windows, window, bounds, 202),
    )
    monkeypatch.setattr(validate_background_mode, "_check_background_state", lambda *args: None)
    monkeypatch.setattr(validate_background_mode, "_warm_capture", lambda **_kwargs: 12.0)
    monkeypatch.setattr(validate_background_mode.win32gui, "GetForegroundWindow", lambda: 202)
    monkeypatch.setattr(validate_background_mode.win32api, "GetCursorPos", lambda: (5, 6))
    monkeypatch.setattr(validate_background_mode.time, "sleep", lambda _seconds: None)

    frame = np.zeros(
        (config.baseline_client_size.height, config.baseline_client_size.width, 4),
        dtype=np.uint8,
    )
    sample = {
        "elapsed_ms": 1.0,
        "timing_ms": {
            "capture": 10.0,
            "refresh": 1.0,
            "sky_stone": 1.0,
            "inventory": 1.0,
            "total": 13.0,
        },
    }
    monkeypatch.setattr(
        validate_background_mode,
        "_sample_viewport",
        lambda **_kwargs: ([sample, sample, sample], frame, frame),
    )
    stable = {
        "stability": {
            "refresh": {"achieved": True},
            "sky_stone": {"achieved": True},
            "inventory": {"achieved": True},
        }
    }
    monkeypatch.setattr(validate_background_mode, "summarize_samples", lambda *_args: stable)
    observed: dict[str, object] = {}

    def fake_observe(**kwargs):
        observed.update(kwargs)
        return (
            frame,
            SimpleNamespace(
                phase_shift_x=0.0,
                phase_shift_y=-float(config.scroll.minimum_upward_shift_px + 1),
                phase_response=1.0,
                changed_fraction=config.scroll.minimum_changed_fraction + 0.01,
                mean_absolute_difference=10.0,
                maximum_difference=100,
            ),
            [{"elapsed_after_last_input_ms": 1.0, "passed": True}],
        )

    monkeypatch.setattr(validate_background_mode, "_observe_scroll_effect", fake_observe)

    class FakeCapture:
        def close(self) -> None:
            pass

    monkeypatch.setattr(validate_background_mode, "_capture_service", lambda _name: FakeCapture())

    class FakeInput:
        def scroll(self, _target: WindowRef, _point: object, _delta: int) -> None:
            pass

    monkeypatch.setattr(validate_background_mode, "Win32WindowMessageInputService", FakeInput)
    result, exit_code = validate_background_mode._run_scroll(
        Namespace(
            sample_count=3,
            interval_ms=config.scroll.interval_ms,
            capture_backend="wgc",
            effect_observation_ms=10000,
        )
    )

    assert exit_code == 0
    assert result["status"] == "ok"
    assert observed["initial_wait_ms"] == 0
    assert result["input"]["initial_observation_wait_ms"] == 0


def test_navigation_validator_clicks_only_entry_and_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = make_config()
    bounds = Rect(10, 20, config.baseline_client_size.width, config.baseline_client_size.height)
    window = WindowRef(101, "game", "game.exe", r"D:\game.exe", 77)
    windows = FakeWindows(WindowState(True, False, False, bounds, bounds))
    vision = SimpleNamespace(
        main_shop_icon=lambda _frame: None,
        shop_ready=lambda _frame: None,
        shop_exit_icon=lambda _frame: None,
    )
    monkeypatch.setattr(
        validate_background_mode,
        "_base_context",
        lambda _args: (config, vision, windows, window, bounds, 202),
    )
    monkeypatch.setattr(validate_background_mode.win32gui, "GetForegroundWindow", lambda: 202)
    monkeypatch.setattr(validate_background_mode.win32api, "GetCursorPos", lambda: (5, 6))
    monkeypatch.setattr(validate_background_mode, "_warm_capture", lambda **_kwargs: 12.0)

    observations = iter(
        [
            SimpleNamespace(anchor=Point(11, 12)),
            SimpleNamespace(anchor=Point(80, 5)),
            SimpleNamespace(anchor=Point(6, 7)),
            SimpleNamespace(anchor=Point(11, 12)),
        ]
    )
    monkeypatch.setattr(
        validate_background_mode,
        "_stable_observation",
        lambda **_kwargs: next(observations),
    )

    class FakeCapture:
        def close(self) -> None:
            pass

    monkeypatch.setattr(validate_background_mode, "_capture_service", lambda _name: FakeCapture())
    clicks: list[tuple[WindowRef, Point]] = []

    class FakeInput:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def click(self, target: WindowRef, point: Point) -> None:
            clicks.append((target, point))

    monkeypatch.setattr(validate_background_mode, "Win32WindowMessageInputService", FakeInput)
    result, exit_code = validate_background_mode._run_navigation(
        Namespace(capture_backend="wgc")
    )

    assert exit_code == 0
    assert result["status"] == "ok"
    assert clicks == [(window, Point(11, 12)), (window, Point(6, 7))]


def test_background_validator_has_no_image_writer_or_focus_mutation() -> None:
    source = (ROOT / "scripts" / "validation" / "validate_background_mode.py").read_text(encoding="utf-8")
    for forbidden in (
        "cv2.imwrite",
        "Image.save",
        "SetForegroundWindow",
        "BringWindowToTop",
        "SetCursorPos",
        "mouse_event",
        "SendInput",
    ):
        assert forbidden not in source
    wrapper = (ROOT / "scripts" / "validation" / "run-admin-background-validation.ps1").read_text(
        encoding="utf-8"
    )
    assert 'ValidateSet("capture", "scroll", "navigation")' in wrapper
    assert 'ValidateSet("wgc")' in wrapper
    assert '"--capture-backend"' in wrapper
    assert '"--effect-observation-ms"' in wrapper
    assert wrapper.index("$arguments = @(") < wrapper.index('if ($Mode -eq "scroll")')
    guide = (ROOT / "docs" / "BACKGROUND_VALIDATION.md").read_text(encoding="utf-8")
    assert "-Mode capture" in guide
    assert "-Mode scroll" in guide
    assert "-Mode navigation" in guide
    assert "-EffectObservationMs 10000" in guide
    assert "不会保存截图" in guide
    assert "没有刷新、刷新确认、购买或购买确认调用路径" in guide
    assert "background-capture-wgc-validation.json" in guide
