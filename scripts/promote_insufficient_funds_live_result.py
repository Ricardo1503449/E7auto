from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import yaml

if __package__:
    from scripts.validate_insufficient_funds import terminal_criteria
else:
    from validate_insufficient_funds import terminal_criteria  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "logs" / "insufficient-funds-live-validation.json"
DEFAULT_OUTPUT = (
    ROOT / "assets" / "templates" / "insufficient_funds_live_validation_manifest.yaml"
)
EXPECTED_SOURCE_SHA256 = "67b68cb53d7f665887d6c16a14fc65d702e5e3f3f999388db900877b5ff1f8a5"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest(source_path: Path = DEFAULT_SOURCE) -> dict[str, object]:
    source = source_path.resolve()
    actual_hash = sha256(source)
    if actual_hash != EXPECTED_SOURCE_SHA256:
        raise RuntimeError(f"Unexpected live-result fingerprint: {actual_hash}")
    raw = json.loads(source.read_text(encoding="utf-8"))

    process = raw.get("process", {})
    window = raw.get("window", {})
    recognition = raw.get("recognition", {})
    terminal = raw.get("terminal_behavior", {})
    raw_criteria = raw.get("criteria", {})
    samples = recognition.get("samples", [])
    required = recognition.get("stable_frames_required")
    threshold = recognition.get("threshold")
    bounds = window.get("client_bounds")
    if process.get("windows_admin") is not True:
        raise RuntimeError("Live result was not captured by an administrator process")
    if window.get("executable_path") != r"D:\Games\EpicSeven\EpicSeven.exe":
        raise RuntimeError("Live result uses the wrong executable")
    if bounds != {"x": 579, "y": 491, "width": 2322, "height": 1306}:
        raise RuntimeError(f"Live result uses unexpected client bounds: {bounds}")
    if recognition.get("template") != "insufficient_funds":
        raise RuntimeError("Live result uses the wrong template")
    if recognition.get("roi") != {"x": 975, "y": 210, "width": 400, "height": 300}:
        raise RuntimeError("Live result uses the wrong recognition ROI")
    if not isinstance(required, int) or required != 3:
        raise RuntimeError("Live result uses the wrong stability requirement")
    if not isinstance(threshold, (int, float)) or float(threshold) != 0.93:
        raise RuntimeError("Live result uses the wrong confidence threshold")
    if len(samples) != 5:
        raise RuntimeError(f"Expected five live samples, found {len(samples)}")
    confidences = [
        float(sample["confidence"])
        for sample in samples
        if sample.get("detected") is True and sample.get("confidence") is not None
    ]
    if len(confidences) != len(samples) or min(confidences) < float(threshold):
        raise RuntimeError("Not every live frame contains a threshold-passing detection")
    if terminal != {
        "stop_reason": "purchase_funds_insufficient",
        "prompt_confirm_clicked": False,
    }:
        raise RuntimeError("Live result has the wrong terminal behavior")
    if raw.get("game_input_sent") is not False or raw.get("screenshots_persisted") is not False:
        raise RuntimeError("Live result violates the no-input/no-screenshot contract")
    expected_criteria = terminal_criteria(stable=len(confidences), required=required)
    if raw_criteria != expected_criteria:
        raise RuntimeError(f"Live result criteria changed: {raw_criteria}")
    if raw.get("status") != "ok" or raw.get("criteria_all_met") is not True:
        raise RuntimeError("Live result did not directly pass its corrected aggregate")
    return {
        "schema_version": 1,
        "validation": "insufficient_funds_live_recognition",
        "status": "operator_confirmed_passed",
        "validated_on": "2026-08-24",
        "source_result": str(source.relative_to(ROOT)).replace("\\", "/"),
        "source_result_sha256": actual_hash,
        "source_recorded_status": raw.get("status"),
        "process": {"windows_admin": True},
        "window": {
            "executable_path": window["executable_path"],
            "title": window["title"],
            "client_bounds": bounds,
            "initial_game_foreground": True,
            "geometry_unchanged": True,
            "foreground_wait_elapsed_ms": float(window["foreground_wait_elapsed_ms"]),
        },
        "recognition": {
            "template": "insufficient_funds",
            "roi": recognition["roi"],
            "threshold": float(threshold),
            "stable_frames_required": required,
            "sample_count": len(samples),
            "detected_count": len(confidences),
            "confidence_minimum": min(confidences),
            "confidence_maximum": max(confidences),
        },
        "terminal_behavior": terminal,
        "criteria": raw_criteria,
        "criteria_all_met": True,
        "raw_observations_preserved": True,
        "game_input_sent": False,
        "screenshots_persisted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Promote a directly passing insufficient-gold live recognition result"
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = build_manifest(args.source)
    args.output.resolve().write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
