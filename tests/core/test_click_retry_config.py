from pathlib import Path

import pytest
import yaml

from e7auto.configuration.loader import load_config
from e7auto.configuration.models import ConfigError
from tests.helpers.paths import ROOT


@pytest.mark.parametrize("change", [
    {"interval_ms": 0}, {"interval_ms": True}, {"max_attempts": 3}, {"max_attempts": 1.5},
    {"minimum_timeout_ms": 5999}, {"extra": 1}, {"missing": "interval_ms"},
])
def test_invalid_click_retry_policy_rejected(monkeypatch, change):
    path = ROOT / "config/internal.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf8"))
    if "missing" in change:
        del raw["click_retry"][change["missing"]]
    else:
        raw["click_retry"].update(change)
    original = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **kw:
                        yaml.safe_dump(raw) if self.resolve() == path.resolve() else original(self, *a, **kw))
    with pytest.raises(ConfigError, match="click_retry"):
        load_config(path)


def test_default_click_retry_policy_is_loaded():
    policy = load_config(ROOT / "config/internal.yaml").click_retry
    assert (policy.interval_ms, policy.max_attempts, policy.minimum_timeout_ms) == (3000, 2, 6000)
