from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import yaml

from e7auto.config import Point, Rect, load_config
from e7auto.vision import OpenCvGameVision, TemplateData, TemplateRepository

from .helpers import make_config


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = ROOT / "assets" / "templates"


class StaticTemplates:
    def __init__(self, templates: dict[str, TemplateData]):
        self.templates = templates

    def get(self, key: str) -> TemplateData:
        return self.templates[key]


def read_png(path: Path) -> np.ndarray:
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    assert image is not None
    return image


def read_supplied_client(path: Path) -> np.ndarray:
    image = read_png(path)
    return np.ascontiguousarray(image[116 : 116 + 1306, 47 : 47 + 2322])


def test_cropped_template_manifest_and_pixels_are_integral() -> None:
    manifest = yaml.safe_load((TEMPLATE_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["method"].startswith("exact pixel crop")
    entries = manifest["templates"]
    assert len(entries) == 10
    assert {entry["state"] for entry in entries} == {
        "unpurchased",
        "confirmation",
        "confirmation_button",
        "purchased",
    }

    for entry in entries:
        output = TEMPLATE_DIR / entry["output_path"]
        assert output.is_file()
        image = read_png(output)
        assert image.shape[:2] == (entry["height"], entry["width"])
        assert image.shape[2] == entry["channels"] == 4

        source = Path(entry["source_path"])
        if source.is_file():
            original = read_png(source)
            expected = original[
                entry["y"] : entry["y"] + entry["height"],
                entry["x"] : entry["x"] + entry["width"],
            ]
            assert np.array_equal(image, expected)


def test_main_shop_icon_template_has_reproducible_foreground_alpha_mask() -> None:
    manifest_path = TEMPLATE_DIR / "main_shop_icon_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    output = TEMPLATE_DIR / manifest["output_path"]
    assert output.is_file()
    image = read_png(output)
    assert image.shape == (
        manifest["output_size"]["height"],
        manifest["output_size"]["width"],
        4,
    )
    alpha = image[:, :, 3]
    assert set(np.unique(alpha)) == {0, 255}
    assert np.count_nonzero(alpha) == manifest["mask"]["foreground_pixels"] == 3273

    source = Path(manifest["source_path"])

    config = replace(make_config(), template_paths={"main_shop_icon": output})
    loaded = TemplateRepository(config).get("main_shop_icon")
    assert loaded.mask is not None
    assert np.array_equal(loaded.mask, alpha)


def test_shop_refresh_template_contains_complete_rounded_button() -> None:
    manifest_path = TEMPLATE_DIR / "shop_refresh_button_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    output = TEMPLATE_DIR / manifest["output_path"]
    assert output.is_file()
    image = read_png(output)
    assert image.shape == (118, 547, 4)
    alpha = image[:, :, 3]
    assert set(np.unique(alpha)) == {0, 255}
    assert np.count_nonzero(alpha) == manifest["mask"]["foreground_pixels"] == 63683
    assert manifest["mask"]["contour_area"] == 63050.5

    source = Path(manifest["source_path"])

    config = replace(make_config(), template_paths={"shop_refresh_button": output})
    loaded = TemplateRepository(config).get("shop_refresh_button")
    assert loaded.mask is not None
    assert np.array_equal(loaded.mask, alpha)

    if source.is_file():
        repository = TemplateRepository(config)
        vision = OpenCvGameVision(config, repository)
        found = vision.match(
            read_png(source),
            "shop_refresh_button",
            Rect(0, 0, 665, 164),
            0.999,
        )
        assert found is not None
        assert found.anchor == Point(317, 87)


def test_shop_exit_template_contains_arrow_and_title_foreground() -> None:
    manifest_path = TEMPLATE_DIR / "shop_exit_icon_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    output = TEMPLATE_DIR / manifest["output_path"]
    assert output.is_file()
    image = read_png(output)
    assert image.shape == (70, 267, 4)
    alpha = image[:, :, 3]
    assert set(np.unique(alpha)) == {0, 255}
    assert np.count_nonzero(alpha) == manifest["mask"]["foreground_pixels"] == 5631
    assert len(manifest["mask"]["component_areas"]) == 5
    assert sum(manifest["mask"]["component_areas"]) == 5631

    source = Path(manifest["source_path"])

    config = replace(make_config(), template_paths={"shop_exit_icon": output})
    repository = TemplateRepository(config)
    loaded = repository.get("shop_exit_icon")
    assert loaded.mask is not None
    assert np.array_equal(loaded.mask, alpha)

    if source.is_file():
        vision = OpenCvGameVision(config, repository)
        found = vision.match(
            read_png(source),
            "shop_exit_icon",
            Rect(0, 0, 353, 105),
            0.999,
        )
        assert found is not None
        assert found.anchor == Point(155, 52)


def test_refresh_confirmation_templates_are_background_free_and_reproducible() -> None:
    manifest_path = TEMPLATE_DIR / "refresh_confirm_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    entries = {entry["role"]: entry for entry in manifest["templates"]}
    assert set(entries) == {"prompt_identity", "confirm_button"}

    prompt_entry = entries["prompt_identity"]
    prompt_path = TEMPLATE_DIR / prompt_entry["output_path"]
    prompt = read_png(prompt_path)
    assert prompt.shape == (41, 426, 4)
    assert set(np.unique(prompt[:, :, 3])) == {0, 255}
    assert np.count_nonzero(prompt[:, :, 3]) == 4904
    assert sum(prompt_entry["mask"]["component_areas"]) == 4904

    button_entry = entries["confirm_button"]
    button_path = TEMPLATE_DIR / button_entry["output_path"]
    button = read_png(button_path)
    assert button.shape == (111, 361, 4)
    assert set(np.unique(button[:, :, 3])) == {0, 255}
    assert np.count_nonzero(button[:, :, 3]) == 37445
    assert button_entry["mask"]["contour_area"] == 37038.0

    source = Path(manifest["source_path"])

    config = replace(
        make_config(),
        template_paths={
            "refresh_confirm_prompt": prompt_path,
            "refresh_confirm_button": button_path,
        },
    )
    repository = TemplateRepository(config)
    assert repository.get("refresh_confirm_prompt").mask is not None
    assert repository.get("refresh_confirm_button").mask is not None

    if source.is_file():
        frame = read_png(source)
        vision = OpenCvGameVision(config, repository)
        prompt_found = vision.match(
            frame,
            "refresh_confirm_prompt",
            Rect(850, 180, 800, 180),
            0.999,
        )
        button_found = vision.match(
            frame,
            "refresh_confirm_button",
            Rect(1100, 400, 650, 250),
            0.999,
        )
        assert prompt_found is not None
        assert prompt_found.anchor == Point(1214, 262)
        assert button_found is not None
        assert button_found.anchor == Point(1408, 512)


def test_insufficient_funds_template_uses_only_terminal_prompt_evidence() -> None:
    manifest_path = TEMPLATE_DIR / "insufficient_funds_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["baseline_client_size"] == {"width": 2322, "height": 1306}
    assert "not negative samples" in manifest["sequence_usage"]
    assert manifest["safety"] == {
        "terminal_stop_reason": "purchase_funds_insufficient",
        "terminal_confirm_clicked": False,
        "full_client_crops_persisted": False,
    }

    expected_origins = {
        "shop": {"x": 46, "y": 103},
        "purchase_confirmation": {"x": 57, "y": 117},
        "insufficient_gold": {"x": 67, "y": 137},
    }
    for key, source_entry in manifest["sources"].items():
        crop = source_entry["client_crop"]
        assert {"x": crop["x"], "y": crop["y"]} == expected_origins[key]
        assert {"width": crop["width"], "height": crop["height"]} == {
            "width": 2322,
            "height": 1306,
        }
    entry = manifest["template"]
    assert entry["source"] == "insufficient_gold"
    output_path = TEMPLATE_DIR / entry["output_path"]
    output = read_png(output_path)
    assert output.shape == (
        entry["output_size"]["height"],
        entry["output_size"]["width"],
        4,
    )
    assert set(np.unique(output[:, :, 3])) == {0, 255}
    assert np.count_nonzero(output[:, :, 3]) == entry["mask"]["foreground_pixels"]
    assert sum(entry["mask"]["component_areas"]) == entry["mask"]["foreground_pixels"]
    assert manifest["positive_evidence"]["confidence"] >= 0.999

    terminal_source = Path(manifest["sources"]["insufficient_gold"]["path"])
    if terminal_source.is_file():
        frame = read_png(terminal_source)
        client_crop = manifest["sources"]["insufficient_gold"]["client_crop"]
        client = frame[
            client_crop["y"] : client_crop["y"] + client_crop["height"],
            client_crop["x"] : client_crop["x"] + client_crop["width"],
        ]
        roi = manifest["calibrated"]["purchase_result_roi"]
        config = replace(
            make_config(),
            template_paths={"insufficient_funds": output_path},
            rois={
                **make_config().rois,
                "purchase_result": Rect(roi["x"], roi["y"], roi["width"], roi["height"]),
            },
            anchor_confidence=manifest["calibrated"]["runtime_threshold"],
        )
        vision = OpenCvGameVision(config, TemplateRepository(config))
        assert vision.purchase_outcome(
            client,
            "covenant_bookmark",
            Rect(970, 145, 240, 220),
        ).value == "insufficient_funds"


def test_sky_stone_templates_are_reproducible_and_parse_supplied_balance() -> None:
    manifest_path = TEMPLATE_DIR / "sky_stone_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["sources"]["balance_crop"]["size"] == {"width": 202, "height": 87}
    assert manifest["sources"]["full_context"]["size"] == {"width": 2428, "height": 1512}
    assert manifest["sources"]["balance_3924"]["size"] == {"width": 185, "height": 91}
    assert manifest["sources"]["balance_3900"]["size"] == {"width": 194, "height": 83}
    assert manifest["sources"]["combined_top_bar"]["size"] == {"width": 471, "height": 84}
    assert manifest["missing_digits"] == []
    assert min(manifest["cross_source_font_similarity"].values()) >= 0.95
    supplemental = manifest["supplemental_validation"]
    assert min(
        entry["similarity"] for entry in supplemental["known_digit_similarity"]
    ) >= supplemental["known_digit_minimum"]
    assert supplemental["zero_repeat_similarity"] >= supplemental["zero_repeat_minimum"]
    assert (
        supplemental["four_cross_source_similarity"]
        >= supplemental["four_cross_source_minimum"]
    )

    icon_entry = manifest["icon"]
    icon_path = TEMPLATE_DIR / icon_entry["output_path"]
    icon = read_png(icon_path)
    assert icon.shape == (75, 62, 4)
    assert set(np.unique(icon[:, :, 3])) == {255}
    assert np.count_nonzero(icon[:, :, 3]) == icon_entry["opaque_pixels"] == 4650
    assert icon_entry["method"] == "exact opaque source pixel crop"
    loaded_icon = TemplateRepository(
        replace(make_config(), template_paths={"sky_stone_icon": icon_path})
    ).get("sky_stone_icon")
    assert loaded_icon.mask is None
    assert np.array_equal(loaded_icon.image, icon[:, :, :3])

    templates = {
        "sky_stone_icon": TemplateData(icon[:, :, :3], None),
    }
    entries = {entry["digit"]: entry for entry in manifest["digits"]}
    assert set(entries) == set("0123456789")
    for digit, entry in entries.items():
        path = TEMPLATE_DIR / entry["output_path"]
        glyph = read_png(path)
        assert set(np.unique(glyph[:, :, 3])) == {0, 255}
        assert np.count_nonzero(glyph[:, :, 3]) == entry["foreground_pixels"]
        templates[f"sky_stone_digit_{digit}"] = TemplateData(
            glyph[:, :, :3], glyph[:, :, 3]
        )

    source_key_by_digit = {
        "0": "balance_3900",
        "4": "combined_top_bar",
        "6": "combined_top_bar",
    }
    for digit, source_key in source_key_by_digit.items():
        entry = entries[digit]
        source_path = Path(manifest["sources"][source_key]["path"])
        if source_path.is_file():
            source_image = read_png(source_path)
            crop = entry["crop"]
            expected_rgb = source_image[
                crop["y"] : crop["y"] + crop["height"],
                crop["x"] : crop["x"] + crop["width"],
                :3,
            ]
            assert np.array_equal(
                read_png(TEMPLATE_DIR / entry["output_path"])[:, :, :3], expected_rgb
            )

    balance_source = manifest["sources"]["balance_crop"]
    source = Path(balance_source["path"])
    if source.is_file():
        source_image = read_png(source)
        crop = icon_entry["crop"]
        expected_icon = source_image[
            crop["y"] : crop["y"] + crop["height"],
            crop["x"] : crop["x"] + crop["width"],
        ]
        assert np.array_equal(icon, expected_icon)
        config = replace(
            make_config(),
            rois={
                **make_config().rois,
                "sky_stone_icon": Rect(0, 0, 75, 87),
                "sky_stone_digits": Rect(70, 20, 105, 46),
            },
            sky_stone_digit_confidence=0.8,
        )
        observation = OpenCvGameVision(config, StaticTemplates(templates)).sky_stone_balance(
            source_image
        )
        assert observation is not None
        assert observation.value == 3927
        assert observation.confidence >= 0.8

    supplied_balances = (
        ("balance_3924", Rect(0, 0, 65, 91), Rect(60, 20, 105, 50), 3924),
        ("balance_3900", Rect(0, 0, 67, 83), Rect(60, 20, 110, 50), 3900),
        ("combined_top_bar", Rect(270, 0, 75, 84), Rect(335, 24, 105, 46), 3867),
    )
    for source_key, icon_roi, digit_roi, expected_value in supplied_balances:
        source_entry = manifest["sources"][source_key]
        source_path = Path(source_entry["path"])
        if not source_path.is_file():
            continue
        source_image = read_png(source_path)
        config = replace(
            make_config(),
            rois={
                **make_config().rois,
                "sky_stone_icon": icon_roi,
                "sky_stone_digits": digit_roi,
            },
            anchor_confidence=0.8,
            sky_stone_digit_confidence=0.8,
        )
        observation = OpenCvGameVision(
            config, StaticTemplates(templates)
        ).sky_stone_balance(source_image)
        assert observation is not None
        assert observation.value == expected_value
        assert observation.confidence >= 0.8

    sequence = "1023456789"
    canvas = np.zeros((80, 400, 3), dtype=np.uint8)
    canvas[: icon.shape[0], : icon.shape[1]] = icon[:, :, :3]
    cursor = 75
    for digit in sequence:
        glyph = read_png(TEMPLATE_DIR / entries[digit]["output_path"])
        y = 5
        target = canvas[y : y + glyph.shape[0], cursor : cursor + glyph.shape[1]]
        foreground = glyph[:, :, 3] > 0
        target[foreground] = glyph[:, :, :3][foreground]
        cursor += glyph.shape[1] + 6
    config = replace(
        make_config(),
        rois={
            **make_config().rois,
            "sky_stone_icon": Rect(0, 0, 62, 75),
            "sky_stone_digits": Rect(70, 0, cursor - 70, 50),
        },
        anchor_confidence=0.99,
        sky_stone_digit_confidence=0.99,
    )
    observation = OpenCvGameVision(config, StaticTemplates(templates)).sky_stone_balance(
        canvas
    )
    assert observation is not None
    assert observation.value == int(sequence)
    assert observation.confidence >= 0.99


def test_sky_stone_alignment_tolerance_parses_supplied_4499_client() -> None:
    source = Path(
        r"C:\Users\lxy\AppData\Local\Temp\codex-clipboard-f682a9cd-e3ce-4b39-9c2d-98be36a6e91d.png"
    )
    if not source.is_file():
        return

    config = replace(
        make_config(),
        rois={
            **make_config().rois,
            "sky_stone_icon": Rect(1555, 12, 90, 95),
            "sky_stone_digits": Rect(1625, 38, 110, 45),
        },
        anchor_confidence=0.93,
        sky_stone_digit_confidence=0.80,
    )
    template_paths = {
        "sky_stone_icon": TEMPLATE_DIR / "sky_stone_icon.png",
        "sky_stone_digit_0_wide": TEMPLATE_DIR / "sky_stone_digit_0_wide.png",
        **{
            f"sky_stone_digit_{digit}": TEMPLATE_DIR / f"sky_stone_digit_{digit}.png"
            for digit in "0123456789"
        },
    }
    config = replace(config, template_paths=template_paths)
    observation = OpenCvGameVision(
        config, TemplateRepository(config)
    ).sky_stone_balance(read_supplied_client(source))

    assert observation is not None
    assert observation.value == 4499
    assert observation.confidence >= 0.80


def test_dynamic_sky_stone_anchor_parses_background_farming_header() -> None:
    source = Path(
        r"C:\Users\lxy\AppData\Local\Temp\codex-clipboard-645c8bb0-f377-4c1d-bb56-01530473a527.png"
    )
    if not source.is_file():
        return

    image = read_png(source)
    client = np.ascontiguousarray(image[91 : 91 + 1306, 38 : 38 + 2322])
    config = load_config(ROOT / "config" / "internal.yaml")
    observation = OpenCvGameVision(
        config, TemplateRepository(config)
    ).sky_stone_balance(client)

    assert observation is not None
    assert observation.value == 3405
    assert observation.confidence >= config.sky_stone_digit_confidence


def test_sky_stone_wide_zero_variant_is_reproducible_and_parses_4501() -> None:
    source = Path(
        r"C:\Users\lxy\AppData\Local\Temp\codex-clipboard-f737e650-4fcb-4974-9950-5c5c9b4274eb.png"
    )
    manifest_path = TEMPLATE_DIR / "sky_stone_zero_wide_manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    template = manifest["template"]
    output = TEMPLATE_DIR / template["output_path"]
    assert output.is_file()
    glyph = read_png(output)
    assert glyph.shape == (33, 23, 4)
    assert np.count_nonzero(glyph[:, :, 3]) == template["foreground_pixels"] == 273
    assert (
        manifest["validation"]["wide_gold_to_sky_stone_zero_similarity"]
        >= manifest["validation"]["wide_gold_to_sky_stone_zero_minimum"]
    )
    if not source.is_file():
        return
    config = replace(
        make_config(),
        rois={
            **make_config().rois,
            "sky_stone_icon": Rect(1555, 12, 90, 95),
            "sky_stone_digits": Rect(1625, 38, 110, 45),
        },
        anchor_confidence=0.93,
        sky_stone_digit_confidence=0.80,
        template_paths={
            "sky_stone_icon": TEMPLATE_DIR / "sky_stone_icon.png",
            "sky_stone_digit_0_wide": output,
            **{
                f"sky_stone_digit_{digit}": TEMPLATE_DIR / f"sky_stone_digit_{digit}.png"
                for digit in "0123456789"
            },
        },
    )
    observation = OpenCvGameVision(
        config, TemplateRepository(config)
    ).sky_stone_balance(read_supplied_client(source))

    assert observation is not None
    assert observation.value == 4501
    assert observation.confidence >= 0.80


def test_wide_zero_extractor_reproduces_checked_in_asset(tmp_path: Path) -> None:
    source = Path(
        r"C:\Users\lxy\AppData\Local\Temp\codex-clipboard-f737e650-4fcb-4974-9950-5c5c9b4274eb.png"
    )
    if not source.is_file():
        return

    from scripts.extract_sky_stone_zero_wide_template import main

    import sys

    original_argv = sys.argv
    try:
        sys.argv = [
            "extract_sky_stone_zero_wide_template.py",
            "--source",
            str(source),
            "--output-dir",
            str(tmp_path),
        ]
        assert main() == 0
    finally:
        sys.argv = original_argv

    assert (tmp_path / "sky_stone_digit_0_wide.png").read_bytes() == (
        TEMPLATE_DIR / "sky_stone_digit_0_wide.png"
    ).read_bytes()
    assert yaml.safe_load(
        (tmp_path / "sky_stone_zero_wide_manifest.yaml").read_text(encoding="utf-8")
    ) == yaml.safe_load(
        (TEMPLATE_DIR / "sky_stone_zero_wide_manifest.yaml").read_text(
            encoding="utf-8"
        )
    )
