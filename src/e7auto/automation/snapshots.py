from __future__ import annotations

from typing import Callable
import threading

from ..domain import RuntimeSnapshot, StopReason


class SnapshotPublisher:
    def __init__(self, initial: RuntimeSnapshot, callback: Callable[[RuntimeSnapshot], None]):
        self._snapshot = initial
        self._callback = callback
        self._lock = threading.Lock()
        self._final_published = False

    @property
    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return self._snapshot

    def mutate(self, mutation: Callable[[RuntimeSnapshot], RuntimeSnapshot]) -> RuntimeSnapshot:
        with self._lock:
            if self._final_published:
                return self._snapshot
            self._snapshot = mutation(self._snapshot)
            value = self._snapshot
        self._callback(value)
        return value

    def finalize(self, reason: StopReason) -> RuntimeSnapshot:
        with self._lock:
            if self._final_published:
                return self._snapshot
            self._snapshot = self._snapshot.finalized(reason)
            self._final_published = True
            value = self._snapshot
        self._callback(value)
        return value
