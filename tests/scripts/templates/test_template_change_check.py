from __future__ import annotations

import json
from pathlib import Path
from tests.helpers.paths import ROOT

import pytest

from scripts.templates import check_changes as guard, register
from tests.helpers.template_projects import project  # noqa: F401


def resources(root: Path) -> dict[str, bytes]:
    return guard.snapshot(root)


def test_unchanged_legacy_templates_do_not_need_retroactive_registration(project):
    root, _, _ = project
    before = resources(root)
    assert guard.check(before, before) == ([], 0)


def test_direct_png_and_hash_update_is_rejected(project):
    root, candidate, _ = project
    before = resources(root)
    png = root / "assets/templates/shop/entry.png"
    png.write_bytes(candidate.read_bytes())
    manifest = root / "assets/templates/shop/manifest.json"
    catalog = json.loads(manifest.read_bytes())
    catalog["templates"]["entry"]["sha256"] = register.file_digest(png)
    manifest.write_bytes(register.encoded(catalog))
    problems, count = guard.check(before, resources(root))
    assert count == 1
    assert any("missing registration source" in p for p in problems)


@pytest.mark.parametrize("new", [False, True])
def test_registered_replacement_and_new_template_pass(project, new):
    root, candidate, source = project
    before = resources(root)
    plan = register.prepare(root, "shop", "new" if new else "entry", candidate, source,
                            "new.png" if new else None)
    register.apply(root, plan)
    assert guard.check(before, resources(root)) == ([], 1)


def test_old_provenance_cannot_cover_new_pixels(project):
    root, candidate, source = project
    register.apply(root, register.prepare(root, "shop", "entry", candidate, source))
    before = resources(root)
    after = dict(before)
    path = "assets/templates/shop/entry.png"
    after[path] = before["assets/templates/shop/other.png"]
    manifest = "assets/templates/shop/manifest.json"
    catalog = json.loads(after[manifest])
    catalog["templates"]["entry"]["sha256"] = guard.digest(after[path])
    after[manifest] = register.encoded(catalog)
    assert any("does not match current template" in p for p in guard.check(before, after)[0])


@pytest.mark.parametrize("remove", [False, True])
def test_source_tampering_or_omission_is_rejected(project, remove):
    root, candidate, source = project
    before = resources(root)
    plan = register.prepare(root, "shop", "entry", candidate, source)
    register.apply(root, plan)
    record = root / plan["destinations"]["record"]
    if remove:
        record.unlink()
    else:
        record.write_bytes(b"changed")
    assert any("record missing or hash mismatch" in p for p in guard.check(before, resources(root))[0])


def test_unregistered_new_png_is_rejected(project):
    root, candidate, _ = project
    before = resources(root)
    (root / "assets/templates/shop/new.png").write_bytes(candidate.read_bytes())
    assert any("not registered" in p for p in guard.check(before, resources(root))[0])


def test_staged_scope_requires_staged_source_and_ignores_worktree_changes(project):
    root, candidate, source = project
    guard.git(root, "init")
    guard.git(root, "config", "core.autocrlf", "true")
    (root / ".gitattributes").write_bytes(
        (ROOT / ".gitattributes").read_bytes()
    )
    guard.git(root, "add", ".gitattributes", "assets", "config")
    guard.git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
              "-c", "commit.gpgsign=false", "commit", "-m", "fixture")
    before = guard.snapshot(root, "HEAD")
    plan = register.prepare(root, "shop", "entry", candidate, source)
    register.apply(root, plan)
    guard.git(root, "add", "assets")
    assert any("record missing" in p for p in guard.check(before, guard.snapshot(root, staged=True))[0])
    guard.git(root, "add", "docs/calibration/registrations")
    guard.git(root, "diff", "--cached", "--check")
    assert guard.check(before, guard.snapshot(root, staged=True)) == ([], 1)
    (root / "assets/templates/shop/entry.png").write_bytes(b"worktree-only damage")
    assert guard.check(before, guard.snapshot(root, staged=True)) == ([], 1)
    assert guard.check(before, resources(root))[0]
    guard.git(root, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
              "-c", "commit.gpgsign=false", "commit", "-m", "registered fixture")
    assert guard.check(before, guard.snapshot(root, "HEAD")) == ([], 1)


def test_bad_base_does_not_silently_pass(project):
    root, _, _ = project
    guard.git(root, "init")
    with pytest.raises(ValueError):
        guard.snapshot(root, "missing-ref")


def test_removed_manifest_cannot_hide_existing_templates(project):
    root, _, _ = project
    before = resources(root)
    (root / "assets/templates/shop/manifest.json").unlink()
    assert any("manifest removed" in p for p in guard.check(before, resources(root))[0])


def test_removing_existing_provenance_is_rejected_even_without_pixel_changes(project):
    root, candidate, source = project
    register.apply(root, register.prepare(root, "shop", "entry", candidate, source))
    before = resources(root)
    after = dict(before)
    path = "assets/templates/shop/manifest.json"
    catalog = json.loads(after[path])
    catalog['templates']['entry']['source'] = {'note': 'removed registration'}
    after[path] = register.encoded(catalog)
    assert any('missing registration source' in p for p in guard.check(before, after)[0])


def test_source_path_escape_is_rejected(project):
    root, candidate, source = project
    before = resources(root)
    register.apply(root, register.prepare(root, "shop", "entry", candidate, source))
    after = resources(root)
    path = "assets/templates/shop/manifest.json"
    catalog = json.loads(after[path])
    catalog['templates']['entry']['source']['record'] = '../outside.yaml'
    after[path] = register.encoded(catalog)
    assert any('does not match current template' in p for p in guard.check(before, after)[0])
