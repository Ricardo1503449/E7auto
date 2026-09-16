from __future__ import annotations

from pathlib import Path
from tests.helpers.paths import ROOT
import json
import subprocess
import sys

import numpy as np
import pytest
import yaml

from e7auto.resources.manifest import load_template_manifest
from scripts.common.candidates import write_candidate_png
from scripts.common.image_io import write_png
from scripts.common import paths
from scripts.templates import register


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for folder in ("assets", "docs", "config")
            for p in (root / folder).rglob("*") if p.is_file()}


from tests.helpers.template_projects import project


def plan_for(project, **kwargs):
    root, candidate, source = project
    return register.prepare(root, kwargs.pop("feature", "shop"), kwargs.pop("key", "entry"), candidate, source, **kwargs)


def test_path_resolution_requires_feature_and_registered_key(project):
    root, _, _ = project
    template_root = root / "assets/templates"
    assert paths.template_relative_path("entry", feature="shop", template_root=template_root) == Path("shop/entry.png")
    with pytest.raises(ValueError, match="Unregistered"):
        paths.template_relative_path("unknown.png", feature="shop", template_root=template_root)
    with pytest.raises(TypeError):
        paths.template_relative_path("entry.png")
    with pytest.raises(ValueError, match="Invalid template feature"):
        paths.template_relative_path("entry", feature="../shop", template_root=template_root)


def test_default_export_is_unique_and_does_not_touch_formal_files(project, monkeypatch):
    root, _, _ = project
    monkeypatch.setattr(paths, "CANDIDATES_DIR", root / "artifacts/template-candidates")
    before = snapshot(root)
    first, provenance = paths.calibration_output_dirs()
    second, _ = paths.calibration_output_dirs()
    assert first != second and first == provenance
    write_candidate_png(first / "shop/entry.png", np.full((3, 4, 3), 10, np.uint8))
    metadata = json.loads((first / "shop/entry.candidate.json").read_text())
    assert metadata["sha256"] == register.file_digest(first / "shop/entry.png")
    assert snapshot(root) == before


@pytest.mark.parametrize("folder", ["assets/templates", "docs/calibration", "config"])
def test_candidate_output_rejects_formal_directories(project, monkeypatch, folder):
    root, _, _ = project
    monkeypatch.setattr(paths, "PROJECT_ROOT", root)
    monkeypatch.setattr(paths, "TEMPLATES_DIR", root / "assets/templates")
    monkeypatch.setattr(paths, "CALIBRATION_DIR", root / "docs/calibration")
    before = snapshot(root)
    with pytest.raises(ValueError, match="formal resources"):
        paths.calibration_output_dirs(root / folder)
    with pytest.raises(ValueError, match="formal resources"):
        write_candidate_png(root / folder / "test.png", np.zeros((2, 2, 3), np.uint8))
    assert snapshot(root) == before


def test_prepare_is_read_only_and_apply_preserves_unrelated_data(project):
    root, candidate, source = project
    before = snapshot(root)
    plan = plan_for(project)
    assert snapshot(root) == before and not (root / "artifacts").exists()
    transaction = register.apply(root, plan)
    catalog = json.loads((root / "assets/templates/shop/manifest.json").read_text())
    old_catalog = json.loads(before["assets/templates/shop/manifest.json"])
    assert catalog["templates"]["other"] == old_catalog["templates"]["other"]
    assert catalog["templates"]["entry"]["label"] == "entry"
    assert catalog["custom_metadata"] == old_catalog["custom_metadata"]
    assert (root / "assets/templates/shop/entry.png").read_bytes() == candidate.read_bytes()
    assert (root / "assets/templates/shop/other.png").read_bytes() == before["assets/templates/shop/other.png"]
    assert (root / "config/internal.yaml").read_bytes() == before["config/internal.yaml"]
    assert (root / plan["destinations"]["record"]).read_bytes() == source.read_bytes()
    load_template_manifest(root / "assets/templates/shop/manifest.json", (64, 48))
    assert register.recover(root, transaction) == "committed"


def test_new_feature_requires_explicit_filename_and_can_be_registered(project):
    root, _, _ = project
    with pytest.raises(ValueError, match="explicit --filename"):
        plan_for(project, feature="future", key="new_entry")
    plan = plan_for(project, feature="future", key="new_entry", filename="controls/new.png")
    assert not (root / "assets/templates/future").exists()
    register.apply(root, plan)
    loaded, _ = load_template_manifest(root / "assets/templates/future/manifest.json", (64, 48))
    assert loaded["new_entry"].name == "new.png"


@pytest.mark.parametrize("filename", ["../escape.png", "nested/../../escape.png", "manifest.json"])
def test_new_template_cannot_escape_or_replace_manifest(project, filename):
    before = snapshot(project[0])
    with pytest.raises(ValueError, match="filename"):
        plan_for(project, key="new_entry", filename=filename)
    assert snapshot(project[0]) == before


def test_existing_corruption_is_not_legitimized_by_registration(project):
    root, _, _ = project
    (root / "assets/templates/shop/entry.png").write_bytes(b"unexpected edit")
    before = snapshot(root)
    with pytest.raises(ValueError, match="integrity mismatch"):
        plan_for(project)
    assert snapshot(root) == before


@pytest.mark.parametrize("change", ["candidate", "source", "manifest", "metadata", "plan"])
def test_changed_inputs_or_formal_state_require_new_preview(project, change):
    root, candidate, source = project
    plan = plan_for(project)
    if change == "candidate":
        write_candidate_png(candidate, np.full((12, 10, 3), 80, np.uint8))
    elif change == "source":
        source.write_text("source: changed\n", encoding="utf-8")
    elif change == "manifest":
        manifest = root / "assets/templates/shop/manifest.json"
        data = json.loads(manifest.read_text())
        data["external_change"] = True
        manifest.write_bytes(register.encoded(data))
    elif change == "metadata":
        metadata = candidate.with_suffix(".candidate.json")
        data = json.loads(metadata.read_text())
        data["external_change"] = True
        metadata.write_bytes(register.encoded(data))
    else:
        plan["catalog"]["templates"]["other"]["label"] = "tampered"
    before = snapshot(root)
    with pytest.raises(ValueError, match="changed"):
        register.apply(root, plan)
    assert snapshot(root) == before


def test_candidate_checksum_and_alpha_are_validated(project):
    _, candidate, _ = project
    candidate.write_bytes(b"edited after export")
    with pytest.raises(ValueError, match="checksum"):
        plan_for(project)
    write_candidate_png(candidate, np.zeros((12, 10, 4), np.uint8))
    with pytest.raises(ValueError, match="empty alpha"):
        plan_for(project)


@pytest.mark.parametrize("image", [np.zeros((12, 10), np.uint8), np.zeros((12, 10, 3), np.uint16)])
def test_unsupported_candidate_pixel_formats_are_rejected(project, image):
    write_candidate_png(project[1], image)
    with pytest.raises(ValueError, match="8-bit color PNG"):
        plan_for(project)


def test_new_key_cannot_take_another_templates_file(project):
    with pytest.raises(ValueError, match="another template"):
        plan_for(project, key="new_entry", filename="other.png")


def test_post_replacement_loader_failure_rolls_back(project, monkeypatch):
    root, candidate, _ = project
    plan = plan_for(project)
    before = snapshot(root)
    original = register.load_template_manifest

    def reject_new(path, baseline):
        if (root / plan["destinations"]["template"]).read_bytes() == candidate.read_bytes():
            raise ValueError("injected final validation failure")
        return original(path, baseline)

    monkeypatch.setattr(register, "load_template_manifest", reject_new)
    with pytest.raises(ValueError, match="rolled back"):
        register.apply(root, plan)
    assert snapshot(root) == before


def test_apply_failure_rolls_back_image_catalog_and_source_record(project, monkeypatch):
    root, _, _ = project
    plan = plan_for(project)
    before = snapshot(root)
    original = register._atomic_write
    failed = False

    def fail_once(path, data):
        nonlocal failed
        if path == root / plan["destinations"]["manifest"] and not failed:
            failed = True
            raise OSError("injected replacement failure")
        return original(path, data)

    monkeypatch.setattr(register, "_atomic_write", fail_once)
    with pytest.raises(ValueError, match="rolled back"):
        register.apply(root, plan)
    assert failed and snapshot(root) == before


def interrupt_after_image(project, monkeypatch):
    root, _, _ = project
    plan = plan_for(project)
    original = register._atomic_write

    def interrupt(path, data):
        original(path, data)
        if path == root / plan["destinations"]["template"]:
            raise KeyboardInterrupt("simulate process interruption after image replacement")

    monkeypatch.setattr(register, "_atomic_write", interrupt)
    with pytest.raises(KeyboardInterrupt):
        register.apply(root, plan)
    monkeypatch.setattr(register, "_atomic_write", original)
    transaction = next((root / "artifacts/template-registration/transactions").iterdir()).name
    return plan, transaction


def test_interrupted_apply_requires_recovery_before_next_registration(project, monkeypatch):
    root, _, _ = project
    before = snapshot(root)
    plan, transaction = interrupt_after_image(project, monkeypatch)
    with pytest.raises(ValueError, match="Recover unfinished"):
        register.apply(root, plan)
    assert register.recover(root, transaction) == "rolled_back"
    assert snapshot(root) == before
    assert register.recover(root, transaction) == "rolled_back"


def test_recovery_never_clobbers_external_changes(project, monkeypatch):
    root, _, _ = project
    plan, transaction = interrupt_after_image(project, monkeypatch)
    (root / plan["destinations"]["template"]).write_bytes(b"external change after interruption")
    before = snapshot(root)
    with pytest.raises(ValueError, match="Recovery conflict"):
        register.recover(root, transaction)
    assert snapshot(root) == before


def test_writer_lock_prevents_overlapping_apply(project):
    root, _, _ = project
    plan = plan_for(project)
    with register._lock(root):
        with pytest.raises(ValueError, match="Another registration"):
            register.apply(root, plan)


def test_cli_prepare_apply_and_recover_round_trip(project):
    root, candidate, source = project
    plan_path = root / "artifacts/template-candidates/20260916-000000-deadbeef/review-plan.json"
    command = [sys.executable, "-B", "-m", "scripts.templates.register", "--project-root", str(root)]

    def run(*args):
        return subprocess.run(command + list(args), cwd=ROOT,
                              capture_output=True, text=True, timeout=20)

    before = snapshot(root)
    source_before = source.read_bytes()
    rejected = run("prepare", "--feature", "shop", "--key", "entry", "--candidate", str(candidate),
                   "--source-record", str(source), "--plan-out", str(source))
    assert rejected.returncode == 1 and "cannot overwrite its inputs" in rejected.stderr
    assert source.read_bytes() == source_before and snapshot(root) == before
    prepared = run("prepare", "--feature", "shop", "--key", "entry", "--candidate", str(candidate),
                   "--source-record", str(source), "--plan-out", str(plan_path))
    assert prepared.returncode == 0, prepared.stderr
    assert plan_path.is_file() and snapshot(root) == before
    applied = run("apply", "--plan", str(plan_path))
    assert applied.returncode == 0, applied.stderr
    result = json.loads(applied.stdout)
    assert result["status"] == "committed"
    recovered = run("recover", "--transaction", result["transaction"])
    assert recovered.returncode == 0 and json.loads(recovered.stdout)["status"] == "committed"
