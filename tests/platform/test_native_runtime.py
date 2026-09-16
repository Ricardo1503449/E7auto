import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from e7auto.platform import native_runtime as runtime
from tests.helpers.paths import ROOT


def test_version_words_are_unsigned(monkeypatch):
    monkeypatch.setattr(runtime.win32api, "GetFileVersionInfo", lambda *_: {
        "FileVersionMS": 917548, "FileVersionLS": -1987379200,
    })
    assert runtime.dll_version(Path("fake.dll")) == (14, 44, 35211, 0)


@pytest.mark.parametrize("version", [(14, 44, 35211, 0), (14, 50, 35719, 0)])
def test_reuses_compatible_loaded_runtime_without_loading_another(monkeypatch, version):
    monkeypatch.setattr(runtime, "loaded_runtime_path", lambda: Path("existing.dll"))
    monkeypatch.setattr(runtime, "dll_version", lambda _: version)
    monkeypatch.setattr(runtime, "runtime_distribution_files", lambda: pytest.fail("should reuse loaded runtime"))
    assert runtime.ensure_msvc_runtime()["version"] == version


def test_old_loaded_runtime_is_rejected_without_attempting_unload_or_replacement(monkeypatch):
    monkeypatch.setattr(runtime, "loaded_runtime_path", lambda: Path("old.dll"))
    monkeypatch.setattr(runtime, "dll_version", lambda _: (14, 29, 30157, 0))
    monkeypatch.setattr(runtime.ctypes, "WinDLL", lambda *_args, **_kwargs: pytest.fail("must not replace an in-use DLL"))
    with pytest.raises(RuntimeError, match="already loaded"):
        runtime.ensure_msvc_runtime()


def test_loads_selected_absolute_dll_and_verifies_result(tmp_path, monkeypatch):
    selected = tmp_path / "msvcp140.dll"
    loaded = iter([None, selected])
    calls = []
    monkeypatch.setattr(runtime, "loaded_runtime_path", lambda: next(loaded))
    monkeypatch.setattr(runtime, "runtime_distribution_files", lambda: {"msvcp140.dll": selected})
    monkeypatch.setattr(runtime, "dll_version", lambda _: runtime.MINIMUM_MSVC_VERSION)
    monkeypatch.setattr(runtime, "_runtime_library", None)
    monkeypatch.setattr(runtime.ctypes, "WinDLL", lambda path, **kwargs: calls.append((path, kwargs)))
    assert runtime.ensure_msvc_runtime()["path"] == str(selected)
    assert calls == [(str(selected), {"winmode": 0x1100})]


@pytest.mark.parametrize("bad", ["missing", "outdated"])
def test_incomplete_or_old_distribution_is_rejected(tmp_path, monkeypatch, bad):
    for name in runtime.MSVC_FILES:
        (tmp_path / name).write_bytes(b"dummy")
    if bad == "missing":
        (tmp_path / "msvcp140.dll").unlink()
    monkeypatch.setattr(runtime, "find_spec", lambda _: SimpleNamespace(origin=str(tmp_path / "__init__.py")))
    monkeypatch.setattr(runtime, "dll_version", lambda _: (14, 29, 0, 0) if bad == "outdated" else runtime.MINIMUM_MSVC_VERSION)
    with pytest.raises(RuntimeError, match="Missing or outdated"):
        runtime.runtime_distribution_files()


@pytest.mark.parametrize("order", ["wgc-first", "qt-first"])
def test_qt_and_real_winrt_share_a_process_and_exit_cleanly(tmp_path, order):
    result = subprocess.run(
        [sys.executable, "-B", "-X", "faulthandler", "-m", "tests.helpers.qt_wgc",
         "--order", order, "--root", str(tmp_path), "--rounds", "10"],
        cwd=ROOT, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, f"Native exit {result.returncode}\n{result.stdout}\n{result.stderr}"
    report = json.loads(result.stdout)
    assert report["order"] == order and report["rounds"] == 10
    assert tuple(report["runtime"]["version"]) >= runtime.MINIMUM_MSVC_VERSION
    assert report["worker_exited"] and report["gc_enabled"] and report["remaining_windows"] == 0


def test_unguarded_old_winrt_import_fails_with_python_error_before_qt():
    code = '''
import winrt.runtime
from e7auto.platform.native_runtime import ensure_msvc_runtime, dll_version, loaded_runtime_path, MINIMUM_MSVC_VERSION
old = dll_version(loaded_runtime_path()) < MINIMUM_MSVC_VERSION
try:
    ensure_msvc_runtime()
except RuntimeError as exc:
    assert old and "already loaded" in str(exc)
else:
    assert not old
'''
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
