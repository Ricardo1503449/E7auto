from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
import cv2

from e7auto import platform_windows
from e7auto.config import DisplayConfig, Point, Rect, Size
from e7auto.domain import StopReason
from e7auto.geometry import AdaptedFrame, CoordinateTransform, adapt_frame, initial_client_size
from e7auto.platform_windows import Win32WindowService, WindowOperationError
from e7auto.ports import DisplayGeometry, WindowRef
from e7auto.vision import OpenCvGameVision, TemplateData

from .helpers import FakeWindowService, ScriptedVision, make_config
from .test_automation import run_session


def test_reference_path_is_identity_and_non_reference_target_uses_width_fraction() -> None:
    baseline = Size(2322, 1306)
    reference = Size(3120, 2080)
    frame = np.zeros((1306, 2322, 4), dtype=np.uint8)
    transform = CoordinateTransform(baseline, baseline)

    assert initial_client_size(reference, reference, baseline, 0.60) == baseline
    assert initial_client_size(Size(2560, 1440), reference, baseline, 0.60) == Size(1536, 864)
    assert adapt_frame(frame, transform) is frame


def test_non_reference_frame_normalizes_each_baseline_roi_once() -> None:
    frame = np.zeros((60, 75, 4), dtype=np.uint8)
    adapted = AdaptedFrame(frame, CoordinateTransform(Size(100, 80), Size(75, 60)))
    roi = Rect(10, 10, 20, 20)

    first = adapted.normalized_roi(roi)
    second = adapted.normalized_roi(roi)

    assert first.shape == (20, 20, 3)
    assert second is first
    assert adapted.normalization_count == 1


def test_non_reference_template_match_returns_baseline_anchor() -> None:
    template = np.arange(75, dtype=np.uint8).reshape(5, 5, 3)
    baseline_frame = np.zeros((80, 100, 3), dtype=np.uint8)
    baseline_frame[22:27, 33:38] = template
    actual_frame = cv2.resize(baseline_frame, (200, 160), interpolation=cv2.INTER_NEAREST)
    adapted = AdaptedFrame(
        actual_frame,
        CoordinateTransform(Size(100, 80), Size(200, 160)),
    )

    class Templates:
        def get(self, _key: str) -> TemplateData:
            return TemplateData(template)

    vision = OpenCvGameVision(make_config(), Templates())  # type: ignore[arg-type]
    found = vision.match(adapted, "needle", Rect(20, 10, 40, 30), 0.99)

    assert found is not None
    assert found.anchor == Point(35, 24)


def test_display_geometry_cross_checks_current_mode_and_skips_mode_query_when_cheap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(platform_windows.win32api, "MonitorFromWindow", lambda *_: 9)
    monkeypatch.setattr(
        platform_windows.win32api,
        "GetMonitorInfo",
        lambda _monitor: {"Monitor": (-1920, 0, 0, 1080), "Device": r"\\.\DISPLAY2"},
    )
    monkeypatch.setattr(
        platform_windows.win32api,
        "EnumDisplaySettings",
        lambda device, mode: calls.append((device, mode))
        or SimpleNamespace(PelsWidth=1920, PelsHeight=1080),
    )
    monkeypatch.setattr(
        platform_windows.ctypes.windll.user32,
        "GetDpiForWindow",
        lambda _hwnd: 144,
    )
    service = Win32WindowService()
    window = WindowRef(1, "game", "game.exe")

    full = service.inspect_display(window, validate_mode=True)
    cheap = service.inspect_display(window, validate_mode=False)

    assert full == DisplayGeometry(9, r"\\.\DISPLAY2", Rect(-1920, 0, 1920, 1080), Size(1920, 1080), 144)
    assert cheap.current_mode is None
    assert len(calls) == 1

    monkeypatch.setattr(
        platform_windows.win32api,
        "EnumDisplaySettings",
        lambda *_: SimpleNamespace(PelsWidth=1919, PelsHeight=1080),
    )
    with pytest.raises(WindowOperationError, match="disagrees"):
        service.inspect_display(window, validate_mode=True)


def test_height_cap_preserves_aspect_and_fits_complete_outer_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = Win32WindowService()
    monkeypatch.setattr(
        service,
        "_adjusted_outer_rect",
        lambda _hwnd, size: platform_windows.wintypes.RECT(0, 0, size.width + 20, size.height + 40),
    )
    baseline = Size(2322, 1306)
    fitted = service.fit_client_size(
        WindowRef(1, "game", "game.exe"),
        Size(1920, 1080),
        baseline,
        Rect(0, 0, 2560, 1080),
    )

    assert fitted.height + 40 <= 1080
    assert abs(fitted.width * baseline.height - fitted.height * baseline.width) <= baseline.width
    next_height = int((fitted.width + 1) * baseline.height / baseline.width + 0.5)
    assert next_height + 40 > 1080


@pytest.mark.parametrize("mode", [Size(2559, 1440), Size(2560, 1439), Size(1440, 2560)])
def test_undersized_display_stops_before_window_mutation_or_input(mode: Size) -> None:
    config = replace(
        make_config(),
        display=DisplayConfig(Size(3120, 2080), Size(2560, 1440), 0.60),
    )
    windows = FakeWindowService(
        display_geometry=DisplayGeometry(
            1,
            r"\\.\DISPLAY1",
            Rect(0, 0, mode.width, mode.height),
            mode,
            96,
        )
    )

    final, _, _, inputs, _, _, _ = run_session(
        ScriptedVision(),
        config=config,
        windows=windows,
    )

    assert final.stop_reason is StopReason.UNSUPPORTED_DISPLAY_RESOLUTION
    assert windows.restore_calls == 0
    assert windows.resize_calls == []
    assert inputs.actions == []


def test_exact_minimum_display_proceeds_with_60_percent_client() -> None:
    config = replace(
        make_config(),
        baseline_client_size=Size(2322, 1306),
        display=DisplayConfig(Size(3120, 2080), Size(2560, 1440), 0.60),
    )
    windows = FakeWindowService(
        display_geometry=DisplayGeometry(
            1,
            r"\\.\DISPLAY1",
            Rect(0, 0, 2560, 1440),
            Size(2560, 1440),
            96,
        )
    )

    final, _, _, _, _, _, _ = run_session(
        ScriptedVision(top=[()], bottom=[()]),
        config=config,
        windows=windows,
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert windows.resize_calls == [Size(1536, 864)]


def test_non_reference_run_scales_input_overlay_and_wraps_capture() -> None:
    config = replace(
        make_config(),
        display=DisplayConfig(Size(400, 400), Size(100, 80), 0.75),
    )
    windows = FakeWindowService(
        display_geometry=DisplayGeometry(
            2,
            r"\\.\DISPLAY2",
            Rect(0, 0, 200, 300),
            Size(200, 300),
            120,
        )
    )
    vision = ScriptedVision(top=[()], bottom=[()])

    final, _, _, inputs, overlay, _, _ = run_session(
        vision,
        config=config,
        windows=windows,
    )

    assert final.stop_reason is StopReason.BUDGET_COMPLETE
    assert windows.fit_calls[0][0] == Size(150, 120)
    assert windows.resize_calls == [Size(150, 120)]
    assert isinstance(vision.scan_frames[0], AdaptedFrame)
    assert [point for action, point, _ in inputs.actions if action == "click"] == [Point(58, 188)]
    assert overlay.calls[0][2] == Point(11, 14)
    assert overlay.calls[0][1][0] == Rect(0, 0, 15, 15)
    assert windows.display_inspections[:4] == [True, True, False, True]


@pytest.mark.parametrize("changed_field", ["mode", "dpi", "monitor"])
def test_runtime_display_change_stops_before_first_input(changed_field: str) -> None:
    class ChangingDisplayWindow(FakeWindowService):
        def inspect_display(self, window: WindowRef, *, validate_mode: bool) -> DisplayGeometry:
            value = super().inspect_display(window, validate_mode=validate_mode)
            if len(self.display_inspections) < 3:
                return value
            if changed_field == "mode" and validate_mode:
                return replace(value, current_mode=Size(399, 400))
            if changed_field == "dpi":
                return replace(value, dpi=120)
            if changed_field == "monitor":
                return replace(value, monitor_id=2, device_name=r"\\.\DISPLAY2")
            return value

    windows = ChangingDisplayWindow()
    final, _, _, inputs, _, _, _ = run_session(ScriptedVision(), windows=windows)

    assert final.stop_reason is StopReason.DISPLAY_CHANGED
    assert inputs.actions == []
    assert windows.resize_calls == [Size(100, 80)]
