"""Opt-in text/map finishing for event choices that award exploration experience."""
from e7auto.core.domain import StopReason
from ..contracts import Detection, MapView
from .context import EventContext


def finish_with_optional_levelup(context: EventContext, *, wait_name: str,
                                choice: Detection | None = None) -> None:
    """Watch for a late popup until the original-floor map is confirmed."""
    levelup_closed = False
    popup_visible = False

    def next_step(frame):
        nonlocal popup_visible
        title = context.vision.control(frame, "levelup")
        close = context.vision.control(frame, "levelup_close")
        popup_visible = title is not None or close is not None
        if popup_visible:
            # A visible or fading popup blocks text and map behind it.
            return close if title is not None and not levelup_closed else None
        text = context.vision.control(frame, "text_continue")
        if text is not None:
            return text
        view = context.vision.map_view(frame)
        return view if view is not None and view.floor == context.floor else None

    while True:
        if choice is not None:
            result = context.choose(choice, next_page=next_step, name=wait_name, moving_text=True,
                                    reason=StopReason.CHAOS_UNKNOWN_CONTENT,
                                    # next_step has just checked both markers in this frame.
                                    retry_blocked=lambda _frame: popup_visible)
            choice = None
        else:
            result = context.wait(wait_name, next_step,
                                  reason=StopReason.CHAOS_UNKNOWN_CONTENT, moving_text=True)
        if isinstance(result, MapView):
            return
        if result.key == "levelup_close":
            # This invocation owns the submitted close, including across recovery.
            levelup_closed = True
        context.tap(result)
