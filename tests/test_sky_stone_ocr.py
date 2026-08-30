from __future__ import annotations

from pathlib import Path

import cv2

from e7auto.config import Rect, load_config
from e7auto.vision import OpenCvGameVision, TemplateRepository, _DigitMatch

from .helpers import make_config


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_2118 = (
    ROOT / "tests" / "fixtures" / "sky_stone_digits_2118_client_1536x864.png"
)
FIXTURE_2115 = (
    ROOT / "tests" / "fixtures" / "sky_stone_digits_2115_client_1536x864.png"
)


def test_digit_acceptance_requires_absolute_quality_and_class_margin() -> None:
    vision = OpenCvGameVision(make_config(), object())  # type: ignore[arg-type]

    assert vision._digit_match_is_safe(_DigitMatch("2", 0.84, 0.70, 0))
    assert not vision._digit_match_is_safe(_DigitMatch("2", 0.79, 0.20, 0))
    assert not vision._digit_match_is_safe(_DigitMatch("2", 0.90, 0.83, 0))


def test_existing_digit_templates_generate_cached_stroke_variants() -> None:
    config = load_config(ROOT / "config" / "internal.yaml")
    vision = OpenCvGameVision(config, TemplateRepository(config))

    first, widths = vision._sky_stone_template_variants()
    second, second_widths = vision._sky_stone_template_variants()

    assert first is second
    assert widths is second_widths
    assert len(first["0"]) == 6
    assert all(len(first[digit]) == 3 for digit in "123456789")
    assert all(
        mask.shape == (48, 32)
        for variants in first.values()
        for mask, _ in variants
    )


def _read_actual_digit_roi(path: Path) -> tuple[int, float] | None:
    config = load_config(ROOT / "config" / "internal.yaml")
    vision = OpenCvGameVision(config, TemplateRepository(config))
    actual_roi = cv2.imread(str(path), cv2.IMREAD_COLOR)
    assert actual_roi is not None
    normalized_roi = cv2.resize(actual_roi, (110, 45), interpolation=cv2.INTER_AREA)
    return vision._read_sky_stone_digits(normalized_roi, Rect(0, 0, 110, 45))


def test_1536_client_digit_roi_parses_real_2118_failure() -> None:
    config = load_config(ROOT / "config" / "internal.yaml")
    parsed = _read_actual_digit_roi(FIXTURE_2118)

    assert parsed is not None
    assert parsed[0] == 2118
    assert parsed[1] >= config.sky_stone_digit_confidence


def test_1536_client_digit_roi_parses_real_2115_failure() -> None:
    config = load_config(ROOT / "config" / "internal.yaml")
    parsed = _read_actual_digit_roi(FIXTURE_2115)

    assert parsed is not None
    assert parsed[0] == 2115
    assert parsed[1] >= config.sky_stone_digit_confidence
