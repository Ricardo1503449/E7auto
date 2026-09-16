from __future__ import annotations
from typing import TypeVar
from e7auto.core.domain import RunState, StopReason
from e7auto.vision.frames import CoordinateTransform, initial_client_size
from e7auto.runtime.stop_control import StopExecution


def prepare(ctx) -> None:
    ctx.transition(RunState.PREPARING)
    ctx.control.checkpoint()
    try:
        elevated = ctx.deps.runtime.is_elevated()
    except Exception as exc:
        raise StopExecution(
            StopReason.PERMISSION_REQUIRED,
            f"cannot verify administrator integrity: {exc}",
        ) from exc
    if not elevated:
        raise StopExecution(
            StopReason.PERMISSION_REQUIRED,
            "automation must run with Windows administrator integrity",
        )
    try:
        window = ctx.deps.windows.locate_unique(
            str(ctx.config.executable_path), ctx.config.window_title
        )
    except Exception as exc:
        raise StopExecution(StopReason.WINDOW_ABNORMAL, str(exc)) from exc
    try:
        display = ctx.deps.windows.inspect_display(window, validate_mode=True)
    except Exception as exc:
        raise StopExecution(StopReason.INVALID_DISPLAY_GEOMETRY, str(exc)) from exc
    if display.current_mode is None:
        raise StopExecution(
            StopReason.INVALID_DISPLAY_GEOMETRY,
            "validated current desktop mode is missing",
        )
    minimum = ctx.config.display.minimum_mode
    if (
        display.current_mode.width < minimum.width
        or display.current_mode.height < minimum.height
    ):
        raise StopExecution(
            StopReason.UNSUPPORTED_DISPLAY_RESOLUTION,
            f"unsupported current desktop mode: {display.current_mode}",
        )
    desired = initial_client_size(
        display.current_mode,
        ctx.config.display.reference_mode,
        ctx.config.baseline_client_size,
        ctx.config.display.client_width_fraction,
    )
    try:
        target = (
            desired
            if display.current_mode == ctx.config.display.reference_mode
            else ctx.deps.windows.fit_client_size(
                window,
                desired,
                ctx.config.baseline_client_size,
                display.monitor_bounds,
            )
        )
        ctx.deps.windows.restore_without_activation(window)
        before_resize = ctx.deps.windows.inspect(window)
        ctx.deps.windows.resize_client(window, target, display.monitor_bounds)
        state = ctx.deps.windows.inspect(window)
    except Exception as exc:
        raise StopExecution(StopReason.WINDOW_ABNORMAL, str(exc)) from exc
    try:
        verified_display = ctx.deps.windows.inspect_display(window, validate_mode=True)
    except Exception as exc:
        raise StopExecution(StopReason.INVALID_DISPLAY_GEOMETRY, str(exc)) from exc
    if verified_display != display:
        raise StopExecution(
            StopReason.DISPLAY_CHANGED,
            f"display changed during preparation: {display} -> {verified_display}",
        )
    if (
        not state.exists
        or state.minimized
        or state.client_bounds.width != target.width
        or state.client_bounds.height != target.height
        or abs(
            state.client_bounds.width * ctx.config.baseline_client_size.height
            - state.client_bounds.height * ctx.config.baseline_client_size.width
        )
        > ctx.config.baseline_client_size.width
        or (
            state.outer_bounds is not None
            and (
                state.outer_bounds.x < display.monitor_bounds.x
                or state.outer_bounds.y < display.monitor_bounds.y
                or state.outer_bounds.right > display.monitor_bounds.right
                or state.outer_bounds.bottom > display.monitor_bounds.bottom
            )
        )
    ):
        raise StopExecution(
            StopReason.WINDOW_ABNORMAL,
            f"client verification failed: {state}",
        )
    ctx.window = window
    ctx.baseline_bounds = state.client_bounds
    ctx.display_geometry = display
    ctx.transform = CoordinateTransform(
        ctx.config.baseline_client_size,
        target,
    )
    if not ctx.deps.overlay.position(
        state.client_bounds,
        ctx.transform.point(ctx.config.overlay_offset),
    ):
        raise StopExecution(
            StopReason.INTERNAL_ERROR,
            "overlay positioning timed out",
        )
    ctx.deps.logger.event(
        "window_prepared",
        client_before_resize=before_resize.client_bounds,
        outer_before_resize=before_resize.outer_bounds,
        outer_after_resize=state.outer_bounds,
        hwnd=window.hwnd,
        client_x=state.client_bounds.x,
        client_y=state.client_bounds.y,
        client_width=state.client_bounds.width,
        client_height=state.client_bounds.height,
        monitor_device=display.device_name,
        monitor_x=display.monitor_bounds.x,
        monitor_y=display.monitor_bounds.y,
        monitor_width=display.monitor_bounds.width,
        monitor_height=display.monitor_bounds.height,
        desktop_width=display.current_mode.width,
        desktop_height=display.current_mode.height,
        dpi=display.dpi,
        game_foreground=state.foreground,
        scale_x=f"{ctx.transform.scale_x:.6f}",
        scale_y=f"{ctx.transform.scale_y:.6f}",
        reference_path=ctx.transform.is_identity,
    )


def ensure_window(ctx, *, full_display_check: bool = False) -> None:
    ctx.control.checkpoint()
    assert (
        ctx.window is not None
        and ctx.baseline_bounds is not None
        and ctx.display_geometry is not None
    )
    try:
        state = ctx.deps.windows.inspect(ctx.window)
    except Exception as exc:
        raise StopExecution(StopReason.WINDOW_ABNORMAL, str(exc)) from exc
    if (
        not state.exists
        or state.minimized
        or state.client_bounds != ctx.baseline_bounds
    ):
        raise StopExecution(StopReason.WINDOW_ABNORMAL, f"window changed: {state}")
    try:
        display = ctx.deps.windows.inspect_display(
            ctx.window,
            validate_mode=full_display_check,
        )
    except Exception as exc:
        raise StopExecution(StopReason.INVALID_DISPLAY_GEOMETRY, str(exc)) from exc
    expected = ctx.display_geometry
    cheap_changed = (
        display.monitor_id != expected.monitor_id
        or display.device_name != expected.device_name
        or display.monitor_bounds != expected.monitor_bounds
        or display.dpi != expected.dpi
    )
    if cheap_changed and not full_display_check:
        try:
            display = ctx.deps.windows.inspect_display(
                ctx.window,
                validate_mode=True,
            )
        except Exception as exc:
            raise StopExecution(StopReason.INVALID_DISPLAY_GEOMETRY, str(exc)) from exc
    if cheap_changed or (
        full_display_check and display.current_mode != expected.current_mode
    ):
        raise StopExecution(
            StopReason.DISPLAY_CHANGED,
            f"display changed during run: {expected} -> {display}",
        )
