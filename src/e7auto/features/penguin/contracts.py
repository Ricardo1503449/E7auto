from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from e7auto.core.observations import Observation

@dataclass(frozen=True, slots=True)
class PenguinDialog:
    price: int | None
    purchase: Observation
    maximum: Observation
    cancel: Observation
    full_price_button: bool


class PenguinVisionPort(Protocol):
    def control(self, frame: object, name: str) -> Observation | None: ...
    def dialog(self, frame: object) -> PenguinDialog | None: ...
    def network_connection_error(self, frame: object) -> Observation | None: ...
    def network_retry(self, frame: object) -> Observation | None: ...
