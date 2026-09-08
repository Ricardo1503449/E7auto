from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from e7auto.config import Rect, Size, load_config
from e7auto.geometry import CoordinateTransform, adapt_frame
from e7auto.vision import OpenCvGameVision, TemplateRepository, _DigitMatch

from .helpers import make_config


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "assets" / "templates"
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


def _paint_balance(
    frame: np.ndarray,
    icon_left: int,
    sequence: str,
    *,
    add_distant_control: bool = True,
) -> int:
    icon = cv2.imread(str(TEMPLATE_DIR / "sky_stone_icon.png"), cv2.IMREAD_UNCHANGED)
    assert icon is not None
    icon_top = 10
    if icon_left >= 0:
        frame[
            icon_top : icon_top + icon.shape[0],
            icon_left : icon_left + icon.shape[1],
        ] = icon[:, :, :3]

    cursor = icon_left + 65
    digit_top = icon_top + 21 if icon_left >= 0 else 5
    for index, digit in enumerate(sequence):
        glyph = cv2.imread(
            str(TEMPLATE_DIR / f"sky_stone_digit_{digit}.png"),
            cv2.IMREAD_UNCHANGED,
        )
        assert glyph is not None
        target = frame[
            digit_top : digit_top + glyph.shape[0],
            cursor : cursor + glyph.shape[1],
        ]
        foreground = glyph[:, :, 3] > 0
        target[foreground] = glyph[:, :, :3][foreground]
        cursor += glyph.shape[1] + 4
        if (len(sequence) - index - 1) % 3 == 0 and index != len(sequence) - 1:
            cursor += 7

    if add_distant_control:
        control_left = cursor + 60
        frame[12:55, control_left : control_left + 43] = 255
    return cursor


def test_production_sky_stone_search_is_full_width_top_bar_only() -> None:
    config = load_config(ROOT / "config" / "internal.yaml")

    assert config.rois["sky_stone_icon"] == Rect(0, 0, 2322, 110)
    assert config.rois["sky_stone_digits"] == Rect(0, 0, 2322, 45)


def test_digit_run_stops_before_distant_header_control_without_digit_limit() -> None:
    config = load_config(ROOT / "config" / "internal.yaml")
    vision = OpenCvGameVision(config, TemplateRepository(config))
    corridor = np.zeros((45, 700, 3), dtype=np.uint8)
    _paint_balance(corridor, -57, "1234567890")

    parsed = vision._read_sky_stone_digits(corridor, Rect(0, 0, 700, 45))

    assert parsed is not None
    assert parsed[0] == 1234567890
    assert parsed[1] >= config.sky_stone_digit_confidence


def test_digit_run_rejects_an_unsafe_component_without_a_group_boundary() -> None:
    config = load_config(ROOT / "config" / "internal.yaml")
    vision = OpenCvGameVision(config, TemplateRepository(config))
    corridor = np.zeros((45, 300, 3), dtype=np.uint8)
    cursor = _paint_balance(corridor, -57, "1624", add_distant_control=False)
    corridor[5:34, cursor + 2 : cursor + 14] = 255

    assert vision._read_sky_stone_digits(corridor, Rect(0, 0, 300, 45)) is None


@pytest.mark.parametrize(
    "actual",
    (Size(2322, 1306), Size(1536, 864), Size(2304, 1296)),
)
def test_dynamic_balance_search_adapts_shifted_seven_digits_across_resolutions(
    actual: Size,
) -> None:
    config = load_config(ROOT / "config" / "internal.yaml")
    baseline = np.zeros(
        (config.baseline_client_size.height, config.baseline_client_size.width, 3),
        dtype=np.uint8,
    )
    _paint_balance(baseline, 950, "1624001")
    raw = cv2.resize(
        baseline,
        (actual.width, actual.height),
        interpolation=cv2.INTER_AREA,
    )
    frame = adapt_frame(
        raw,
        CoordinateTransform(config.baseline_client_size, actual),
    )

    observation = OpenCvGameVision(
        config,
        TemplateRepository(config),
    ).sky_stone_balance(frame)

    assert observation is not None
    assert observation.value == 1624001
    assert observation.confidence >= config.sky_stone_digit_confidence
