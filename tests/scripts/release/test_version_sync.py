from __future__ import annotations

from pathlib import Path
import importlib.metadata
import json
import re
import sys
import tomllib

import pytest

from e7auto import __version__
from e7auto import app
from scripts import verify_environment
from scripts.release import verify_release
from tests.helpers.paths import ROOT
from tests.helpers.update_install import configure_installed_update


def _project_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]


def test_active_source_document_and_environment_versions_are_synchronized() -> None:
    version = _project_version()

    assert __version__ == version
    assert importlib.metadata.version("e7auto") == version
    assert f"当前检入源码版本为 `v{version}`" in (ROOT / "README.md").read_text(
        encoding="utf-8"
    )
    assert (ROOT / "docs" / "user" / "使用说明.md").read_text(
        encoding="utf-8"
    ).splitlines()[0] == f"# E7auto v{version} x64 使用说明"
    assert re.search(
        rf"^## v{re.escape(version)} — \d{{4}}-\d{{2}}-\d{{2}}$",
        (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
        re.MULTILINE,
    )


def test_packaged_usage_guide_contains_window_message_risk_notice() -> None:
    guide = (ROOT / "docs" / "user" / "使用说明.md").read_text(encoding="utf-8")

    assert "Windows Graphics Capture" in guide
    assert "Win32 窗口消息" in guide
    assert "永久封禁" in guide
    assert "自愿承担全部风险" in guide


def test_source_self_check_reports_the_project_version(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(app, "project_root", lambda: ROOT)
    monkeypatch.setattr(app, "validate_source_environment", lambda _root: None)
    monkeypatch.setattr(app, "validate_wgc_import", lambda: (True, ""))
    monkeypatch.setattr(sys, "argv", ["e7auto", "--self-check"])
    monkeypatch.setattr(app, "validate_installation", lambda _root: pytest.fail('source mode must skip installed validation'))

    assert app.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["version"] == _project_version()
    assert report["update_installation_valid"] is None
    assert report["update_installation_error"] == ""


@pytest.mark.parametrize('failure', [None, 'executable', 'api', 'registry', 'manifest'])
def test_compiled_self_check_runs_the_production_update_precheck(tmp_path, monkeypatch, capsys, failure):
    from e7auto.platform import update_install
    from e7auto.ui import reporting_check

    root = tmp_path / '安装 路径'
    configure_installed_update(root, monkeypatch)
    (root / 'config').mkdir()
    (root / 'config/internal.yaml').touch()
    (root / 'assets/templates').mkdir(parents=True)
    (root / 'assets/ui').mkdir()
    for name in ('e7auto.ico', 'e7auto-icon-256.png', 'shop-card-background.png'):
        (root / 'assets/ui' / name).touch()
    monkeypatch.setattr(app, 'project_root', lambda: root)
    monkeypatch.setattr(app, 'validate_wgc_import', lambda: (True, ''))
    monkeypatch.setattr(reporting_check, 'check_reporting_runtime',
                        lambda _root, **kwargs: {'statistics_runtime_ready': True})
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(root / 'python.exe'))
    monkeypatch.setattr(sys, 'argv', ['E7auto.exe', '--self-check'])
    if failure == 'executable':
        monkeypatch.setattr(update_install.win32api, 'GetModuleFileName', lambda _module: str(root / 'renamed.exe'))
    elif failure == 'api':
        def fail(_module):
            raise update_install.win32api.error(126, 'GetModuleFileName', 'unavailable')
        monkeypatch.setattr(update_install.win32api, 'GetModuleFileName', fail)
    elif failure == 'registry':
        monkeypatch.setattr(update_install.winreg, 'QueryValueEx', lambda *args: (str(tmp_path), 1))
    elif failure == 'manifest':
        (root / 'install-files.txt').write_text('wrong-product', encoding='utf-8')

    assert app.main() == (0 if failure is None else 2)
    report = json.loads(capsys.readouterr().out)
    assert report['compiled'] is True
    assert report['update_installation_valid'] is (failure is None)
    assert bool(report['update_installation_error']) is (failure is not None)


def test_environment_verifier_requires_exact_versions(capsys) -> None:
    assert verify_environment.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["project_version"] == _project_version()
    assert report["source_version"] == _project_version()
    assert report["installed_project_version"] == _project_version()
    assert report["locked_packages_missing"] == []
    assert report["locked_packages_mismatched"] == []


def test_future_build_embeds_and_verifies_windows_version_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    version = _project_version()
    expected = verify_release.windows_version(version)
    major, minor, patch, build = expected
    info = {
        "FileVersionMS": (major << 16) | minor,
        "FileVersionLS": (patch << 16) | build,
        "ProductVersionMS": (major << 16) | minor,
        "ProductVersionLS": (patch << 16) | build,
    }
    monkeypatch.setattr(
        verify_release.win32api,
        "GetFileVersionInfo",
        lambda _path, _query: info,
    )

    assert verify_release.verify_windows_versions(tmp_path / "E7auto.exe", version) == []
    mismatched_patch = patch ^ 1
    info["ProductVersionLS"] = (mismatched_patch << 16) | build
    assert verify_release.verify_windows_versions(
        tmp_path / "E7auto.exe", version
    ) == [
        f"executable product version is {(major, minor, mismatched_patch, build)}, "
        f"expected {expected}"
    ]

    def missing_version(_path, _query):
        raise verify_release.pywintypes.error(
            1813,
            "GetFileVersionInfo",
            "The specified resource type cannot be found",
        )

    monkeypatch.setattr(
        verify_release.win32api,
        "GetFileVersionInfo",
        missing_version,
    )
    assert verify_release.verify_windows_versions(
        tmp_path / "E7auto.exe", version
    )[0].startswith("unable to read executable version information:")

    build_script = (ROOT / "scripts" / "release" / "build-standalone.ps1").read_text(
        encoding="utf-8"
    )
    assert '"--file-version=$windowsVersion"' in build_script
    assert '"--product-version=$windowsVersion"' in build_script
    assert "--product-name=E7auto" in build_script
    assert '"--file-description=E7auto Windows x64 shop automation"' in build_script


def test_source_test_runner_keeps_qt_and_wgc_in_one_process() -> None:
    runner = (ROOT / "scripts" / "test-source.ps1").read_text(encoding="utf-8")

    assert runner.count("& $python -m pytest") == 1
    assert "--ignore" not in runner
    assert "test_wgc_capture.py" not in runner
