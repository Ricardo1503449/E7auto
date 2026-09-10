from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QApplication, QLabel
import pytest

from e7auto.ui import MainWindow
from scripts.release.verify_release import (
    verify_forbidden_release_files,
    verify_required_release_files,
    verify_ui_assets,
)
from tests.helpers.paths import ROOT


def test_ui_assets_and_standalone_build_are_wired() -> None:
    assert verify_ui_assets(ROOT / "assets" / "ui") == []
    application = QApplication.instance() or QApplication([])
    window = MainWindow(ROOT)
    try:
        window.show()
        application.processEvents()
        assert not window.windowIcon().isNull()
        assert not window._function_center_page.shop_card._pixmap.isNull()
        title_bar_icon = window.findChild(QLabel, "titleBarIcon")
        assert title_bar_icon is not None
        title_bar_pixmap = title_bar_icon.pixmap()
        assert title_bar_pixmap is not None
        expected_dpr = title_bar_icon.devicePixelRatioF()
        assert title_bar_pixmap.devicePixelRatioF() == pytest.approx(expected_dpr)
        assert title_bar_pixmap.width() == round(title_bar_icon.width() * expected_dpr)
        assert title_bar_pixmap.height() == round(title_bar_icon.height() * expected_dpr)
    finally:
        window.close()
        application.processEvents()
    build_script = (ROOT / "scripts" / "release" / "build-standalone.ps1").read_text(
        encoding="utf-8"
    )
    assert "--windows-icon-from-ico=$appIcon" in build_script
    assert "--include-data-dir=assets/ui=assets/ui" in build_script
    assert "--include-package=winrt.windows.foundation" in build_script
    assert "--include-module=winrt._winrt_windows_foundation" in build_script
    assert "--noinclude-dlls=cv2/opencv_videoio_ffmpeg*.dll" in build_script
    assert (
        "--noinclude-dlls=PySide6/qt-plugins/imageformats/qpdf.dll"
        in build_script
    )
    assert "--noinclude-dlls=qt6pdf.dll" in build_script
    assert '"E7auto_v${version}_x64.zip"' in build_script
    assert "Compress-Archive" in build_script
    assert "Failed builds/archives never reach this cleanup" in build_script
    assert "Remove-Item -LiteralPath $resolvedOldReleaseZip -Force" in build_script
    assert build_script.index("Compress-Archive") < build_script.index(
        "Remove-Item -LiteralPath $resolvedOldReleaseZip -Force"
    )


def test_release_verifier_rejects_approved_forbidden_files(tmp_path: Path) -> None:
    forbidden = (
        tmp_path / "cv2" / "opencv_videoio_ffmpeg500_64.dll",
        tmp_path / "PySide6" / "qt-plugins" / "imageformats" / "qpdf.dll",
        tmp_path / "qt6pdf.dll",
    )
    for path in forbidden:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    assert verify_forbidden_release_files(tmp_path) == [
        "forbidden release file was bundled: cv2/opencv_videoio_ffmpeg500_64.dll",
        "forbidden release file was bundled: PySide6/qt-plugins/imageformats/qpdf.dll",
        "forbidden release file was bundled: qt6pdf.dll",
    ]


def test_release_verifier_requires_winrt_foundation_projection(
    tmp_path: Path,
) -> None:
    assert verify_required_release_files(tmp_path) == [
        "missing required release file: winrt/_winrt_windows_foundation.pyd"
    ]

    foundation = tmp_path / "winrt" / "_winrt_windows_foundation.pyd"
    foundation.parent.mkdir(parents=True)
    foundation.touch()

    assert verify_required_release_files(tmp_path) == []
