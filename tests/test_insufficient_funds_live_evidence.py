from __future__ import annotations

from pathlib import Path

import yaml

from scripts.promote_insufficient_funds_live_result import build_manifest


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    ROOT / "assets" / "templates" / "insufficient_funds_live_validation_manifest.yaml"
)


def test_live_evidence_records_direct_operator_confirmed_pass() -> None:
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["status"] == "operator_confirmed_passed"
    assert manifest["source_recorded_status"] == "ok"
    assert manifest["raw_observations_preserved"] is True
    assert manifest["recognition"]["sample_count"] == 5
    assert manifest["recognition"]["detected_count"] == 5
    assert manifest["recognition"]["confidence_minimum"] > 0.999999
    assert manifest["terminal_behavior"] == {
        "stop_reason": "purchase_funds_insufficient",
        "prompt_confirm_clicked": False,
    }
    assert all(manifest["criteria"].values())
    assert manifest["criteria_all_met"] is True
    assert manifest["game_input_sent"] is False
    assert manifest["screenshots_persisted"] is False

    source = ROOT / manifest["source_result"]
    if source.is_file():
        assert build_manifest(source) == manifest
