from __future__ import annotations

from pathlib import Path

from e7auto.config import Rect, load_config
from scripts.validate_live_recognition import (
    first_stable_run,
    load_read_only_commissioning_config,
    summarize_samples,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "internal.yaml"


def test_read_only_commissioning_loader_does_not_relax_production_gate() -> None:
    production = load_config(CONFIG_PATH)
    assert production.rois["purchase_result"] == Rect(975, 210, 400, 300)

    config = load_read_only_commissioning_config(CONFIG_PATH)
    assert config.scroll.delta == -120
    assert config.scroll.repetitions == 6
    assert config.timing.stable_frames == 3
    assert config.timing.refresh_timeout_ms == 10000
    assert config.template_paths["insufficient_funds"].name == "insufficient_funds.png"
    assert config.rois["purchase_result"] == Rect(975, 210, 400, 300)
    assert config.overlay_offset.x == config.overlay_offset.y == 0


def test_first_stable_run_rejects_invalid_values_and_accepts_empty_scan() -> None:
    assert first_stable_run((False, True, True, True), 3, bool) == (3, True)
    assert first_stable_run((None, 3825, 3825, 3825), 3, lambda value: value is not None) == (
        3,
        3825,
    )
    assert first_stable_run((("transient",), (), (), ()), 3) == (3, ())
    assert first_stable_run((True, False, True, True), 3, bool) is None


def test_sample_summary_uses_production_consecutive_identical_rule() -> None:
    samples = []
    inventory_keys = (
        (("top_1", "covenant_bookmark"),),
        (),
        (("top_1", "covenant_bookmark"),),
        (("top_1", "covenant_bookmark"),),
        (("top_1", "covenant_bookmark"),),
    )
    for index, inventory_key in enumerate(inventory_keys, start=1):
        samples.append(
            {
                "elapsed_ms": float(index * 100),
                "timing_ms": {
                    "capture": 1.0,
                    "refresh": 2.0,
                    "sky_stone": 3.0,
                    "inventory": 4.0,
                    "total": 10.0,
                },
                "refresh": {"detected": True, "confidence": 0.99},
                "sky_stone": {"detected": True, "value": 3825, "confidence": 0.9},
                "matches": [
                    {"slot_id": slot, "target_id": target, "confidence": 0.98}
                    for slot, target in inventory_key
                ],
            }
        )

    summary = summarize_samples(samples, 3)
    assert summary["stability"]["refresh"]["sample"] == 3
    assert summary["stability"]["sky_stone"]["value"] == 3825
    assert summary["stability"]["inventory"]["sample"] == 5
    assert summary["observed_targets"] == ["covenant_bookmark"]
    assert summary["timing"]["total"]["mean_ms"] == 10.0
