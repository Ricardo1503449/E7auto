from pathlib import Path
import shutil

import pytest

from e7auto.platform.native_runtime import MINIMUM_MSVC_VERSION, dll_version, runtime_distribution_files
from scripts.release.native_runtime import synchronize_native_runtime, verify_native_runtime
from tests.helpers.paths import ROOT


def test_build_runtime_normalization_updates_root_and_nested_copies(tmp_path):
    nested = tmp_path / "winrt"
    nested.mkdir()
    (nested / "MSVCP140.dll").write_bytes(b"outdated placeholder")
    synchronize_native_runtime(tmp_path)
    sources = runtime_distribution_files()
    for name, source in sources.items():
        assert (tmp_path / name).read_bytes() == source.read_bytes()
    assert (nested / "MSVCP140.dll").read_bytes() == sources["msvcp140.dll"].read_bytes()
    assert verify_native_runtime(tmp_path) == []


def test_release_check_rejects_an_old_private_winrt_runtime(tmp_path):
    synchronize_native_runtime(tmp_path)
    nested = tmp_path / "winrt"
    nested.mkdir()
    old = ROOT / ".venv/Lib/site-packages/winrt/MSVCP140.dll"
    assert dll_version(old) < MINIMUM_MSVC_VERSION
    shutil.copyfile(old, nested / "msvcp140.dll")
    assert any("outdated MSVC runtime" in error for error in verify_native_runtime(tmp_path))


def test_sync_refuses_to_modify_existing_project_release():
    with pytest.raises(ValueError, match="restricted to build/nuitka"):
        synchronize_native_runtime(ROOT / "dist")


def test_build_normalizes_runtime_before_publishing():
    script = (ROOT / "scripts/release/build-standalone.ps1").read_text(encoding="utf-8")
    guard = script.index("Build publication paths are outside")
    normalize = script.index("-m scripts.release.native_runtime")
    remove_previous = script.index("if (Test-Path -LiteralPath $resolvedReleaseDir)")
    publish = script.index("Move-Item -LiteralPath $compiledRelease")
    assert guard < normalize < remove_previous < publish
