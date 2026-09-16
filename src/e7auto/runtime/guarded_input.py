from __future__ import annotations
from typing import Callable, TypeVar
from e7auto.core.types import Point
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


def interruptible_wait(ctx, seconds: int) -> None:
    deadline = ctx.deps.clock.monotonic() + seconds
    while True:
        ctx.ensure_window()
        remaining = deadline - ctx.deps.clock.monotonic()
        if remaining <= 0:
            return
        ctx.deps.clock.sleep(min(1.0, remaining))
        ctx.control.checkpoint()
