"""Daily installation activity, independent of per-run shop counters."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import re

FEATURES = ('shop', 'penguin', 'chaos')
BEIJING = timezone(timedelta(hours=8))


def activity_day(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, BEIJING).date().isoformat()


def version_key(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})', value):
        raise ValueError('invalid activity version')
    return tuple(int(part) for part in value.split('.'))


@dataclass(frozen=True, slots=True)
class UsageDayReport:
    installation_id: str
    date: str
    version: str
    features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not re.fullmatch(r'[0-9a-f]{32}', self.installation_id):
            raise ValueError('invalid installation identifier')
        if date.fromisoformat(self.date).isoformat() != self.date:
            raise ValueError('invalid activity date')
        version_key(self.version)
        if len(set(self.features)) != len(self.features) or any(value not in FEATURES for value in self.features):
            raise ValueError('invalid activity feature')

    def payload(self) -> dict:
        return {'installation_id': self.installation_id, 'date': self.date,
                'version': self.version, 'features': sorted(self.features)}
