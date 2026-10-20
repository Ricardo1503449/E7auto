from __future__ import annotations
from typing import Callable, TypeVar
import math
import sys
from e7auto.core.types import Point, ClientPoint
from e7auto.core.domain import StopReason
from e7auto.core.ports import WindowRef
from e7auto.runtime.stop_control import StopExecution


def dispatch_input(
    ctx,
    action: str,
    point: Point,
    callback: Callable[[WindowRef, Point], None],
    **fields: object,
) -> None:
    assert ctx.transform is not None
    client_point = ctx.transform.point(point)

    def guarded() -> None:
        ctx.ensure_window(full_display_check=True)
        assert ctx.window is not None
        try:
            callback(ctx.window, client_point)
        except StopExecution:
            raise
        except Exception as exc:
            ctx.deps.logger.event(
                "input_failed",
                action=action,
                logical_x=point.x,
                logical_y=point.y,
                client_x=client_point.x,
                client_y=client_point.y,
                error=repr(exc),
            )
            raise StopExecution(
                StopReason.INPUT_FAILURE,
                f"platform input failed for {action}: {exc}",
            ) from exc
        ctx.deps.logger.event(
            "input",
            action=action,
            logical_x=point.x,
            logical_y=point.y,
            client_x=client_point.x,
            client_y=client_point.y,
            background_message_queued=True,
            **fields,
        )

    ctx.control.dispatch(guarded)


def click(ctx, action: str, point: Point, **fields: object) -> None:
    ctx.dispatch_input(
        action,
        point,
        ctx.deps.inputs.click,
        **fields,
    )


def drag(ctx, action: str, start: Point, end: Point, *, duration_ms: int, step_ms: int,
         frame_guard: Callable[[object], bool] | None = None,
         step_guard: Callable[[], bool] | None = None,
         allow_outside_client: bool = False, frame_check_ms: int = 0) -> bool:
    """A drag is interruptible between messages; release always precedes recovery.

    Return False when recovery or a page-guard change interrupts the gesture.
    Its caller must recapture before deciding whether another gesture is needed.
    """
    if type(duration_ms) is not int or type(step_ms) is not int or not 0 < step_ms <= duration_ms:
        raise ValueError("invalid drag timing")
    if type(frame_check_ms) is not int or not 0 <= frame_check_ms <= 100:
        raise ValueError("invalid drag frame check interval")
    ctx.control.checkpoint()
    assert ctx.transform is not None
    if isinstance(start,ClientPoint) != isinstance(end,ClientPoint):
        start,end = ctx.transform.point(start),ctx.transform.point(end)
    for point in (start, end):
        client = ctx.transform.point(point)
        if not (-32768 <= client.x <= 32767 and -32768 <= client.y <= 32767):
            raise StopExecution(StopReason.INPUT_FAILURE, "drag coordinate exceeds signed message range")
        if (point == start or not allow_outside_client) and not (
            0 <= client.x < ctx.transform.actual.width and 0 <= client.y < ctx.transform.actual.height
        ):
            raise StopExecution(StopReason.INPUT_FAILURE, "drag coordinate outside client")
    if step_guard is not None and not step_guard():
        return False
    frame = ctx.capture_raw()
    error_detector = ctx.deps.vision.network_connection_error
    if ctx.vision_call(error_detector, frame) is not None:
        ctx.handle_network_exception(frame)
        return False
    if frame_guard is not None and not frame_guard(frame):
        return False
    last_checked = ctx.active_monotonic()
    assert ctx.transform is not None
    pressed = False
    owner = None
    last_point = ctx.transform.point(start)
    interrupted = None
    aborted = False

    def down(window, point):
        nonlocal pressed, owner, last_point
        ctx.deps.inputs.pointer_down(window, point)
        owner, last_point, pressed = window, point, True

    def move(window, point):
        nonlocal last_point
        if window != owner:
            raise StopExecution(StopReason.WINDOW_ABNORMAL, "drag owner changed")
        if allow_outside_client:
            ctx.deps.inputs.pointer_move(window, point, allow_outside_client=True)
        else:
            ctx.deps.inputs.pointer_move(window, point)
        last_point = point

    try:
        ctx.dispatch_input(action+"_down", start, down)
        steps = math.ceil(duration_ms/step_ms)
        last_sent = ctx.active_monotonic()
        for step in range(1, steps+1):
            ctx.control.checkpoint()
            # Never compress later intervals to catch up after slow capture or
            # window checks: each movement gets its requested minimum interval.
            due = last_sent + duration_ms/1000/steps
            remaining = due-ctx.active_monotonic()
            if remaining > 1e-6:
                ctx.deps.clock.sleep(remaining)
            ctx.control.checkpoint()
            if step_guard is not None and not step_guard():
                aborted = True
                break
            if frame_check_ms == 0 or ctx.active_monotonic()-last_checked >= frame_check_ms/1000:
                frame = ctx.capture_raw()
                if ctx.vision_call(error_detector, frame) is not None:
                    interrupted = frame
                    break
                if frame_guard is not None and not frame_guard(frame):
                    aborted = True
                    break
                last_checked = ctx.active_monotonic()
            if step_guard is not None and not step_guard():
                aborted = True
                break
            point_type = ClientPoint if isinstance(start,ClientPoint) and isinstance(end,ClientPoint) else Point
            point = point_type(round(start.x+(end.x-start.x)*step/steps),
                          round(start.y+(end.y-start.y)*step/steps))
            ctx.dispatch_input(action+"_move", point, move, drag_step=step, drag_steps=steps)
            last_sent = ctx.active_monotonic()
    finally:
        if pressed:
            unwinding = sys.exc_info()[0] is not None
            try:
                # This is release-only cleanup, permitted even after a manual stop. No move,
                # click, retry or navigation is appended to a stopped gesture.
                ctx.deps.inputs.pointer_up(owner, last_point)
                ctx.deps.logger.event("drag_released", action=action, cleanup=True,
                                      client_x=last_point.x, client_y=last_point.y)
            except Exception as exc:
                try:
                    ctx.deps.logger.event("drag_release_failed", action=action, error=repr(exc))
                except Exception:
                    pass  # Cleanup diagnostics must not hide the original stop.
                if not unwinding:
                    raise StopExecution(StopReason.INPUT_FAILURE, f"drag release failed: {exc}") from exc
    ctx.control.checkpoint()
    if frame_check_ms and interrupted is None and not aborted:
        # A short burst can finish before the next frame check. Recheck after
        # releasing, before its caller can treat the page as unchanged.
        frame = ctx.capture_raw()
        if ctx.vision_call(error_detector, frame) is not None:
            interrupted = frame
        elif frame_guard is not None and not frame_guard(frame):
            aborted = True
    if interrupted is not None:
        ctx.handle_network_exception(interrupted)
        return False
    return not aborted


def interruptible_wait(ctx, seconds: int) -> None:
    deadline = ctx.deps.clock.monotonic() + seconds
    while True:
        ctx.ensure_window()
        remaining = deadline - ctx.deps.clock.monotonic()
        if remaining <= 0:
            return
        ctx.deps.clock.sleep(min(1.0, remaining))
        ctx.control.checkpoint()
