from __future__ import annotations
from dataclasses import asdict
from e7auto.core.domain import RunState, StopReason
from e7auto.core.ports import WindowGeometry, WindowGeometryChanged, WindowLookupError
from e7auto.core.types import Rect, Size
from e7auto.vision.frames import CoordinateTransform, initial_client_size
from e7auto.runtime.stop_control import StopExecution


_PREPARE_TIMEOUT_SECONDS = 2.0
_GEOMETRY_POLL_SECONDS = 0.02
_GEOMETRY_STABLE_SECONDS = 0.1
_MAX_RESIZE_ATTEMPTS = 3


def _diagnostic(ctx, event: str, **fields: object) -> None:
    try:
        ctx.deps.logger.event(event, **fields)
    except Exception:
        pass  # Diagnostics cannot replace a preparation result or stop reason.


def _preparation_error(stage: str, detail: str) -> StopExecution:
    return StopExecution(StopReason.WINDOW_ABNORMAL, f"window preparation failed: stage={stage}; {detail}")


def _check_budget(ctx, deadline: float, stage: str) -> None:
    ctx.control.checkpoint()
    if ctx.deps.clock.monotonic() >= deadline:
        raise _preparation_error(stage, "window geometry did not reach the target within 2 seconds")


def _prepare_guard(ctx, window, display, deadline: float, stage: str) -> None:
    _check_budget(ctx, deadline, stage)
    try:
        current = ctx.deps.windows.inspect_display(window, validate_mode=True)
    except StopExecution:
        raise
    except Exception as exc:
        raise StopExecution(StopReason.INVALID_DISPLAY_GEOMETRY, str(exc)) from exc
    _check_budget(ctx, deadline, stage)
    if current != display:
        raise StopExecution(
            StopReason.DISPLAY_CHANGED,
            f"display changed during preparation: {display} -> {current}",
        )


def _sample_geometry(ctx, window, display, deadline: float, stage: str) -> WindowGeometry | None:
    _prepare_guard(ctx, window, display, deadline, stage)
    try:
        geometry = ctx.deps.windows.measure_geometry(window)
    except WindowGeometryChanged:
        _prepare_guard(ctx, window, display, deadline, stage)
        return None
    except StopExecution:
        raise
    except Exception as exc:
        raise _preparation_error(stage, str(exc)) from exc
    _prepare_guard(ctx, window, display, deadline, stage)
    try:
        geometry.frame_margins
        if geometry.window_dpi <= 0 or geometry.thread_dpi_awareness != 2:
            raise ValueError(f"invalid physical-pixel measurement: {geometry}")
    except ValueError as exc:
        raise _preparation_error(stage, str(exc)) from exc
    return geometry


def _poll_geometry(ctx, deadline: float, stage: str) -> None:
    _check_budget(ctx, deadline, stage)
    remaining = max(0.0, deadline - ctx.deps.clock.monotonic())
    ctx.deps.clock.sleep(min(_GEOMETRY_POLL_SECONDS, remaining))
    _check_budget(ctx, deadline, stage)


def _fit_client_size(desired: Size, baseline: Size, margins: tuple[int, int, int, int], monitor: Rect) -> Size:
    _, top, _, bottom = margins
    if desired.height + top + bottom <= monitor.height:
        return desired
    low, high, best = 1, desired.width, None
    while low <= high:
        width = (low + high) // 2
        candidate = Size(width, max(1, int(width * baseline.height / baseline.width + 0.5)))
        if candidate.height + top + bottom <= monitor.height:
            best, low = candidate, width + 1
        else:
            high = width - 1
    if best is None:
        raise ValueError("no positive fixed-aspect client fits monitor height")
    return best


def _requested_outer(geometry: WindowGeometry, target: Size, monitor: Rect) -> Rect:
    left, top, right, bottom = geometry.frame_margins
    width, height = target.width + left + right, target.height + top + bottom
    if width > monitor.width or height > monitor.height:
        raise ValueError(f"complete target window does not fit monitor: {Size(width, height)}")
    outer = geometry.state.outer_bounds
    assert outer is not None
    return Rect(
        min(max(outer.x, monitor.x), monitor.right - width),
        min(max(outer.y, monitor.y), monitor.bottom - height),
        width, height,
    )


def _at_target(geometry: WindowGeometry, target: Size, baseline: Size, monitor: Rect) -> bool:
    client, outer = geometry.state.client_bounds, geometry.state.outer_bounds
    assert outer is not None
    return (
        Size(client.width, client.height) == target
        and abs(client.width * baseline.height - client.height * baseline.width) <= baseline.width
        and outer.x >= monitor.x and outer.y >= monitor.y
        and outer.right <= monitor.right and outer.bottom <= monitor.bottom
    )


def _prepare_geometry(ctx, window, display, desired: Size):
    deadline = ctx.deps.clock.monotonic() + _PREPARE_TIMEOUT_SECONDS
    stage, attempts = "measure", 0
    target, requested, geometry = None, None, None
    try:
        while geometry is None:
            geometry = _sample_geometry(ctx, window, display, deadline, stage)
            if geometry is None:
                _poll_geometry(ctx, deadline, stage)
        before_resize = geometry.state
        stage = "fit"
        target = (
            desired if display.current_mode == ctx.config.display.reference_mode
            else _fit_client_size(desired, ctx.config.baseline_client_size, geometry.frame_margins,
                                  display.monitor_bounds)
        )
        write_required = True
        stable_key, stable_since = None, ctx.deps.clock.monotonic()
        while True:
            if write_required:
                stage = "set_outer_rect"
                requested = _requested_outer(geometry, target, display.monitor_bounds)
                _diagnostic(
                    ctx, "window_resize_requested", attempt=attempts + 1,
                    target_client=target, frame_margins=geometry.frame_margins,
                    measured=asdict(geometry), requested_outer=requested,
                )
                _prepare_guard(ctx, window, display, deadline, stage)
                attempts += 1
                ctx.control.dispatch(
                    lambda: ctx.deps.windows.set_outer_rect(window, requested, display.monitor_bounds),
                )
                _prepare_guard(ctx, window, display, deadline, stage)
                stage = "verify"
                observed = None
                while observed is None:
                    observed = _sample_geometry(ctx, window, display, deadline, stage)
                    if observed is None:
                        _poll_geometry(ctx, deadline, stage)
                geometry = observed
                client = geometry.state.client_bounds
                _diagnostic(
                    ctx, "window_resize_observed", attempt=attempts,
                    target_client=target, requested_outer=requested, measured=asdict(geometry),
                    width_difference=target.width - client.width,
                    height_difference=target.height - client.height,
                )
                _prepare_guard(ctx, window, display, deadline, stage)
                if _at_target(geometry, target, ctx.config.baseline_client_size, display.monitor_bounds):
                    return target, before_resize, geometry.state
                stable_key, stable_since = geometry.stability_key, ctx.deps.clock.monotonic()
                write_required = False
            stage = "settle"
            _poll_geometry(ctx, deadline, stage)
            observed = _sample_geometry(ctx, window, display, deadline, stage)
            if observed is None:
                stable_key = None
                continue
            geometry = observed
            if _at_target(geometry, target, ctx.config.baseline_client_size, display.monitor_bounds):
                _diagnostic(ctx, "window_resize_settled", attempt=attempts, target_client=target,
                            requested_outer=requested, measured=asdict(geometry))
                _prepare_guard(ctx, window, display, deadline, stage)
                return target, before_resize, geometry.state
            now = ctx.deps.clock.monotonic()
            if geometry.stability_key != stable_key:
                stable_key, stable_since = geometry.stability_key, now
            if attempts < _MAX_RESIZE_ATTEMPTS and now - stable_since >= _GEOMETRY_STABLE_SECONDS:
                write_required = True
    except StopExecution as exc:
        _diagnostic(ctx, "window_preparation_failed", stage=stage, attempts=attempts,
                    target_client=target, requested_outer=requested,
                    measured=asdict(geometry) if geometry is not None else None, detail=exc.detail)
        raise
    except Exception as exc:
        _diagnostic(ctx, "window_preparation_failed", stage=stage, attempts=attempts,
                    target_client=target, requested_outer=requested,
                    measured=asdict(geometry) if geometry is not None else None, detail=str(exc))
        raise _preparation_error(stage, str(exc)) from exc


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
            ctx.config.game_clients, ctx.config.window_title
        )
    except WindowLookupError as exc:
        reason = StopReason.MULTIPLE_GAME_WINDOWS if exc.matches else StopReason.GAME_WINDOW_NOT_FOUND
        try:
            ctx.deps.logger.event(
                "game_window_lookup_rejected",
                match_count=len(exc.matches),
                window_title=ctx.config.window_title,
                configured_clients=[asdict(client) for client in ctx.config.game_clients],
                candidates=[asdict(window) for window in exc.matches],
            )
        except Exception:
            pass  # A diagnostic failure must not replace the startup rejection.
        raise StopExecution(reason, str(exc)) from exc
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
        ctx.deps.windows.restore_without_activation(window)
    except StopExecution:
        raise
    except Exception as exc:
        raise _preparation_error("restore", str(exc)) from exc
    target, before_resize, state = _prepare_geometry(ctx, window, display, desired)
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
    _diagnostic(
        ctx, "window_prepared",
        client_before_resize=before_resize.client_bounds,
        outer_before_resize=before_resize.outer_bounds,
        outer_after_resize=state.outer_bounds,
        hwnd=window.hwnd,
        client_id=window.client_id,
        process_name=window.process_name,
        executable_path=window.executable_path,
        process_id=window.process_id,
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
