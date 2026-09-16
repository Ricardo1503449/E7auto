"""Feature isolation and portable resource-layout regression tests."""
from pathlib import Path
import json
import shutil

import pytest
import yaml

from e7auto.config import COMMON_TEMPLATE_KEYS, ConfigError, load_config
from e7auto.penguin_vision import with_penguin_config
from e7auto.vision import TemplateRepository
from scripts.common.paths import CALIBRATION_DIR, calibration_output_dirs
from scripts.release.verify_release import verify_template_assets
from tests.helpers.paths import ROOT


@pytest.fixture
def installation(tmp_path):
    """Model the distributable layout without source docs or historical assets."""
    shutil.copytree(ROOT / "assets/templates", tmp_path / "assets/templates")
    (tmp_path / "config").mkdir()
    shutil.copyfile(ROOT / "config/internal.yaml", tmp_path / "config/internal.yaml")
    return tmp_path


def test_penguin_loads_without_any_shop_images(installation):
    shop = installation / "assets/templates/shop"
    for path in shop.glob("*.png"):
        path.unlink()
    config = with_penguin_config(load_config(
        installation / "config/internal.yaml", template_profile="penguin"
    ))
    assert len(config.template_paths) == 26
    assert set(config.template_paths) == COMMON_TEMPLATE_KEYS | {
        key for key in config.template_paths if key.startswith("penguin_")
    }
    repository = TemplateRepository(config)
    assert repository.get("penguin_sanctuary_entry").image.size
    with pytest.raises(ConfigError, match="main_shop_icon.*does not exist"):
        load_config(installation / "config/internal.yaml")


def test_shop_loads_without_penguin_assets_or_calibration_docs(installation):
    for path in (installation / "assets/templates/penguin").iterdir():
        path.unlink()
    config = load_config(installation / "config/internal.yaml")
    assert len(config.template_paths) == 31
    assert not any(key.startswith("penguin_") for key in config.template_paths)
    assert TemplateRepository(config).get("main_shop_icon").image.size


def test_shared_column_search_configuration_is_required_for_both_features(installation):
    path = installation / "config/internal.yaml"
    content = yaml.safe_load(path.read_text(encoding="utf-8"))
    del content["rois"]["left_icon_column"]
    path.write_text(yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ConfigError, match="rois.left_icon_column is required"):
        load_config(path, template_profile="penguin")
    with pytest.raises(ConfigError, match="rois.left_icon_column is required"):
        load_config(path, template_profile="shop")


@pytest.mark.parametrize("profile", ["shop", "penguin"])
@pytest.mark.parametrize("filename", [
    "common/digits/sky_stone_digit_0_wide.png", "common/network/network_retry.png"
])
def test_missing_shared_image_rejects_both_features(installation, profile, filename):
    (installation / "assets/templates" / filename).unlink()
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(installation / "config/internal.yaml", template_profile=profile)


@pytest.mark.parametrize("profile", ["shop", "penguin"])
def test_corrupt_shared_image_is_rejected_before_vision(installation, profile):
    (installation / "assets/templates/common/digits/sky_stone_digit_1.png").write_bytes(b"invalid")
    with pytest.raises(ConfigError, match="template integrity mismatch: sky_stone_digit_1"):
        load_config(installation / "config/internal.yaml", template_profile=profile)


def test_changed_penguin_template_is_rejected(installation):
    (installation / "assets/templates/penguin/01_sanctuary_entry.png").write_bytes(b"invalid")
    config = load_config(installation / "config/internal.yaml", template_profile="penguin")
    with pytest.raises(ConfigError, match="template integrity mismatch"):
        with_penguin_config(config)
    assert any("integrity mismatch" in problem for problem in verify_template_assets(
        installation / "assets/templates"
    ))


def test_changed_shop_template_is_rejected_before_runtime(installation):
    (installation / "assets/templates/shop/main_shop_icon.png").write_bytes(b"invalid")
    with pytest.raises(ConfigError, match="template integrity mismatch: main_shop_icon"):
        load_config(installation / "config/internal.yaml")


def test_manifest_source_coordinates_do_not_override_runtime_search(installation):
    path = installation / "assets/templates/penguin/manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["templates"]["sanctuary_entry"]["source"]["crop"] = [9999, 9999, 1, 1]
    data["templates"]["forest_entry"]["source"]["crop"] = [0, 0, 1, 1]
    path.write_text(json.dumps(data), encoding="utf-8")
    config = load_config(installation / "config/internal.yaml", template_profile="penguin")
    assert with_penguin_config(config).rois == config.rois


@pytest.mark.parametrize("profile", ["shop", "penguin"])
def test_template_manifest_paths_cannot_escape_feature_directory(installation, profile):
    path = installation / "assets/templates" / profile / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    first = next(iter(data["templates"].values()))
    first["file"] = "../outside.png"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid template path"):
        config = load_config(installation / "config/internal.yaml", template_profile=profile)
        if profile == "penguin":
            with_penguin_config(config)


def test_release_verification_uses_source_provenance_for_portable_installation(installation):
    assert not (installation / "docs").exists()
    assert verify_template_assets(installation / "assets/templates") == []
    (installation / "assets/templates/penguin/manifest.json").unlink()
    assert any("Template manifest" in p and "penguin" in p for p in verify_template_assets(
        installation / "assets/templates"
    ))


def test_custom_calibration_output_and_manifest_directory(tmp_path):
    output, manifest = calibration_output_dirs(tmp_path / "export")
    assert output == manifest == tmp_path / "export"
    output, manifest = calibration_output_dirs(tmp_path / "export", tmp_path / "records")
    assert output.is_dir() and manifest.is_dir()
    assert manifest == tmp_path / "records"


def test_registration_and_release_verification_use_current_catalog_and_source(installation):
    import numpy as np
    from scripts.common.candidates import write_candidate_png
    from scripts.templates import register

    shutil.copytree(ROOT / "docs/calibration", installation / "docs/calibration")
    candidate = installation / "artifacts/template-candidates/20260916-000000-deadbeef/candidate.png"
    write_candidate_png(candidate, np.full((12, 10, 3), 123, np.uint8))
    source = installation / "artifacts/source.yaml"
    source.write_text("source: synthetic registration integration test\n", encoding="utf-8")
    plan = register.prepare(installation, "shop", "additional_control", candidate, source, "additional.png")
    register.apply(installation, plan)
    assert verify_template_assets(installation / "assets/templates", installation / "docs/calibration") == []
    (installation / plan["destinations"]["record"]).write_text("changed\n", encoding="utf-8")
    assert any("source record integrity mismatch" in problem for problem in verify_template_assets(
        installation / "assets/templates", installation / "docs/calibration",
    ))


def test_provenance_outputs_match_runtime_configuration():
    config = load_config(ROOT / "config/internal.yaml")
    referenced = set(config.template_paths.values())

    def outputs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "output_path":
                    yield item
                else:
                    yield from outputs(item)
        elif isinstance(value, list):
            for item in value:
                yield from outputs(item)

    paths = set()
    for manifest in CALIBRATION_DIR.rglob("*"):
        if manifest.suffix not in {".yaml", ".json"}:
            continue
        text = manifest.read_text(encoding="utf-8")
        data = json.loads(text) if manifest.suffix == ".json" else yaml.safe_load(text)
        paths.update((ROOT / "assets/templates" / name).resolve() for name in outputs(data))
    assert len(paths) == 29
    assert paths <= referenced
    assert all(path.is_file() for path in paths)
