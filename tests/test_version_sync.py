from __future__ import annotations

import importlib.metadata
import json
import re
import sys
import tomllib
from pathlib import Path

from e7auto import __version__
from e7auto import app
from scripts import verify_environment, verify_release


ROOT = Path(__file__).resolve().parents[1]


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
    assert (ROOT / "docs" / "使用说明.txt").read_text(
        encoding="utf-8"
    ).splitlines()[0] == f"E7auto v{version} x64 使用说明"
    assert re.search(
        rf"^## v{re.escape(version)} — \d{{4}}-\d{{2}}-\d{{2}}$",
        (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
        re.MULTILINE,
    )


def test_packaged_usage_guide_contains_window_message_risk_notice() -> None:
    guide = (ROOT / "docs" / "使用说明.txt").read_text(encoding="utf-8")

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

    assert app.main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["version"] == _project_version()


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
    info["ProductVersionLS"] = 0
    assert verify_release.verify_windows_versions(
        tmp_path / "E7auto.exe", version
    ) == [f"executable product version is {(major, minor, 0, 0)}, expected {expected}"]

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

    build_script = (ROOT / "scripts" / "build-standalone.ps1").read_text(
        encoding="utf-8"
    )
    assert '"--file-version=$windowsVersion"' in build_script
    assert '"--product-version=$windowsVersion"' in build_script
    assert "--product-name=E7auto" in build_script
    assert '"--file-description=E7auto Windows x64 shop automation"' in build_script


def test_source_test_runner_isolates_wgc_from_qt() -> None:
    runner = (ROOT / "scripts" / "test-source.ps1").read_text(encoding="utf-8")

    first = "& $python -m pytest --ignore=tests/test_wgc_capture.py"
    second = "& $python -m pytest tests/test_wgc_capture.py"
    assert first in runner
    assert second in runner
    assert runner.index(first) < runner.index(second)
