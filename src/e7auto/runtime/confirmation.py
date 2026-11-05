"""Single-use handoff guarded by capture, input, recovery and window identity."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class Confirmation(Generic[T]):
    value: T
    captures: int
    inputs: int
    recovery: int
    transform: object
    window: object
    consumed: bool = False

    @classmethod
    def record(cls, runtime, value: T) -> Confirmation[T]:
        return cls(value, runtime.capture_count, runtime.input_generation,
                   runtime.network_recovery_generation, runtime.transform, runtime.window)

    def take(self, runtime) -> T | None:
        runtime.control.checkpoint()
        used, self.consumed = self.consumed, True
        if (used or self.captures != runtime.capture_count
                or self.inputs != runtime.input_generation
                or self.recovery != runtime.network_recovery_generation
                or self.transform is not runtime.transform or self.window != runtime.window):
            return None
        return self.value
