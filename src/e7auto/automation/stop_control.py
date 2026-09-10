from __future__ import annotations

from typing import Callable, TypeVar
import threading

from ..domain import StopReason


T = TypeVar("T")


class StopExecution(RuntimeError):
    def __init__(self, reason: StopReason, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason.value)


class StopController:
    """Serializes stop requests with input dispatch.

    If F5 wins the lock, subsequent input is rejected. If a single input call already
    owns the lock, that call is considered dispatched and cannot be retracted.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
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
        with self._lock:
            if self._reason is not None:
                raise StopExecution(self._reason)
            return action()
