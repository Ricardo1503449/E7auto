from __future__ import annotations

from typing import Callable, TypeVar
import threading

from e7auto.core.domain import StopReason


T = TypeVar("T")


class StopExecution(RuntimeError):
    def __init__(self, reason: StopReason, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason.value)


class StopController:
    """Serialize input admission without holding the stop-state lock during I/O.

    If a stop request wins admission, subsequent input is rejected. An admitted
    input may finish, including its release-only cleanup. Requests never wait
    for that input's platform calls or logging.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._dispatch_lock = threading.RLock()
        self._reason: StopReason | None = None

    def request(self, reason: StopReason) -> bool:
        with self._lock:
            if self._reason is not None:
                return False
            self._reason = reason
            return True

    @property
    def reason(self) -> StopReason | None:
        with self._lock:
            return self._reason

    def checkpoint(self) -> None:
        with self._lock:
            if self._reason is not None:
                raise StopExecution(self._reason)

    def dispatch(self, action: Callable[[], T]) -> T:
        with self._dispatch_lock:
            self.checkpoint()
            return action()
