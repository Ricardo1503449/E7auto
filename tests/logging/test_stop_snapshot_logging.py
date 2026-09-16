from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np
import pytest

from e7auto.config import LoggingConfig
from e7auto.ports import CachedGameFrame
from e7auto.run_logging import RunLogManager


def cached():
    image = np.arange(8 * 6 * 4, dtype=np.uint8).reshape(6, 8, 4)
    image[:, :, 3] = 0  # Alpha must not hide the RGB evidence in an image viewer.
    return CachedGameFrame(image, "2026-09-15T17:01:53.123+08:00", 10.0)


def save(log, frame=None):
    log.save_stop_snapshot(cached() if frame is None else frame,
                           stop_reason="scroll_verification_failed", stopped_monotonic=12.5)


def result_lines(log):
    return [line for line in log.path.read_text(encoding="utf-8").splitlines()
            if "event=stop_snapshot " in line]


def test_png_matches_raw_client_pixels_in_unicode_directory_and_logs_metadata(tmp_path):
    log = RunLogManager(tmp_path / "异常截图", LoggingConfig()).start("color")
    frame = cached()
    original = frame.frame.copy()
    save(log, frame)
    save(log, frame)
    log.event("run_stopped", reason="scroll_verification_failed")
    log.close()
    image_path = log.path.with_name(log.path.stem + "-stop.png")
    decoded = cv2.imdecode(np.frombuffer(image_path.read_bytes(), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    np.testing.assert_array_equal(decoded, original[:, :, :3])
    np.testing.assert_array_equal(frame.frame, original)
    assert not list(log.path.parent.glob("*.tmp"))
    lines = result_lines(log)
    assert len(lines) == 1
    for field in ["outcome=saved", f"path={image_path.name}", "frame_age_ms=2500",
                  "captured_at=2026-09-15T17:01:53.123+08:00", "width=8", "height=6",
                  "source=last_successful_capture", "stop_reason=scroll_verification_failed"]:
        assert field in lines[0]
    assert "event=run_stopped" in log.path.read_text(encoding="utf-8").splitlines()[-1]


def test_no_frame_skips_without_encoding_or_fabricated_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(cv2, "imencode", lambda *_: pytest.fail("No cache must not encode"))
    log = RunLogManager(tmp_path, LoggingConfig()).start("empty")
    log.save_stop_snapshot(None, stop_reason="hotkey_failure", stopped_monotonic=1)
    save(log)  # At most one attempt even when the first attempt had no cache.
    log.close()
    lines = result_lines(log)
    assert len(lines) == 1 and "outcome=skipped" in lines[0]
    assert "detail=no_cached_frame" in lines[0]
    for field in ("width=", "height=", "captured_at=", "frame_age_ms=", " path="):
        assert field not in lines[0]
    assert not list(tmp_path.glob("*.png"))


@pytest.mark.parametrize("failure", ["encode_false", "encode_raise", "open", "write", "rename"])
def test_save_failure_is_one_diagnostic_and_removes_own_partial_file(tmp_path, monkeypatch, failure):
    log = RunLogManager(tmp_path, LoggingConfig()).start("failure")
    original_open = Path.open

    def fail(*args, **kwargs):
        raise OSError("synthetic disk/codec failure")

    if failure == "encode_false":
        monkeypatch.setattr(cv2, "imencode", lambda *_: (False, None))
    elif failure == "encode_raise":
        monkeypatch.setattr(cv2, "imencode", fail)
    elif failure in ("open", "write"):
        class BrokenWrite:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def write(self, data):
                self.stream.write(data[:5])
                raise OSError("partial PNG write")

        def open_file(path, *args, **kwargs):
            if path.name.endswith(".png.tmp"):
                if failure == "open": fail()
                return BrokenWrite(original_open(path, *args, **kwargs))
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", open_file)
    else:
        monkeypatch.setattr(Path, "rename", fail)
    save(log)
    save(log)
    log.event("run_stopped", reason="original_failure", detail="original detail")
    log.close()
    assert not list(tmp_path.glob("*.png*"))
    assert len(result_lines(log)) == 1
    assert "outcome=failed" in result_lines(log)[0]
    assert "path=" not in result_lines(log)[0]
    assert "reason=original_failure" in log.path.read_text(encoding="utf-8").splitlines()[-1]


@pytest.mark.parametrize("extension", [".png", ".png.tmp"])
def test_existing_snapshot_or_temporary_is_never_overwritten(tmp_path, extension):
    log = RunLogManager(tmp_path, LoggingConfig()).start("conflict")
    existing = log.path.with_name(log.path.stem + "-stop" + extension)
    existing.write_bytes(b"existing evidence")
    save(log)
    log.close()
    assert existing.read_bytes() == b"existing evidence"
    assert "outcome=failed" in result_lines(log)[0]


def test_temporary_cleanup_failure_is_recorded_with_original_save_error(tmp_path, monkeypatch):
    log = RunLogManager(tmp_path, LoggingConfig()).start("cleanup")
    unlink = Path.unlink

    def fail_rename(*_): raise OSError("rename failed")
    def fail_unlink(path, *args, **kwargs):
        if path.name.endswith(".png.tmp"): raise OSError("cleanup denied")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "rename", fail_rename)
    monkeypatch.setattr(Path, "unlink", fail_unlink)
    save(log)
    log.close()
    assert len(result_lines(log)) == 1
    assert "rename failed" in result_lines(log)[0]
    assert "temporary cleanup failed" in result_lines(log)[0]
    assert len(list(tmp_path.glob("*.png.tmp"))) == 1


def test_rotation_does_not_duplicate_snapshot_or_change_its_name(tmp_path):
    log = RunLogManager(tmp_path, LoggingConfig()).start("rotate")
    save(log)
    expected = log.path.with_name(log.path.stem + "-stop.png")
    image = expected.read_bytes()
    log.handler.maxBytes = 1400
    for _ in range(5): log.event("large", text="x" * 1000)
    save(log)
    log.close()
    assert list(tmp_path.glob("*.png")) == [expected]
    assert expected.read_bytes() == image


OLD = "run-20000101-120000-000001-old"


def artifacts(directory, stem=OLD):
    paths = [directory / (stem + suffix) for suffix in (".log", ".log.1", "-stop.png", "-stop.png.tmp")]
    for path in paths:
        path.write_bytes(b"old")
        os.utime(path, (1_600_000_000, 1_600_000_000))
    return paths


def test_retention_deletes_complete_old_group_and_preserves_unrelated_images(tmp_path):
    old = artifacts(tmp_path)
    unrelated = tmp_path / "run-my-photo-stop.png"
    unrelated.write_bytes(b"unrelated")
    log = RunLogManager(tmp_path, LoggingConfig()).start("new")
    log.close()
    assert all(not path.exists() for path in old)
    assert unrelated.exists()


def test_snapshot_size_counts_toward_group_capacity(tmp_path):
    old = artifacts(tmp_path)
    for path in old: os.utime(path, None)
    old[0].write_bytes(b"x" * 600_000)
    old[2].write_bytes(b"x" * 600_000)
    log = RunLogManager(tmp_path, LoggingConfig(max_total_mb=1)).start("new")
    log.close()
    assert all(not path.exists() for path in old)


def test_snapshot_is_not_an_extra_run_and_extends_group_last_modified(tmp_path):
    old = artifacts(tmp_path)
    os.utime(old[2], None)
    log = RunLogManager(tmp_path, LoggingConfig(keep_runs=2)).start("new")
    log.close()
    assert all(path.exists() for path in old)


@pytest.mark.parametrize("suffix", ["-stop.png", "-stop.png.tmp"])
def test_orphan_snapshot_and_temporary_are_still_retained_as_run_groups(tmp_path, suffix):
    orphan = tmp_path / (OLD + suffix)
    orphan.write_bytes(b"orphan")
    os.utime(orphan, (1_600_000_000, 1_600_000_000))
    log = RunLogManager(tmp_path, LoggingConfig()).start("new")
    log.close()
    assert not orphan.exists()


def test_partial_group_removal_can_clean_orphan_on_next_pass(tmp_path, monkeypatch):
    old = artifacts(tmp_path)
    unlink = Path.unlink

    def fail_image(path, *args, **kwargs):
        if path == old[2]: raise PermissionError("image locked")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_image)
    log = RunLogManager(tmp_path, LoggingConfig()).start("new")
    assert old[2].exists() and not old[0].exists()
    monkeypatch.setattr(Path, "unlink", unlink)
    log.close()
    assert not old[2].exists()
    assert "image locked" in log.path.read_text(encoding="utf-8")


def test_active_run_snapshot_is_protected_across_processes(tmp_path):
    log = RunLogManager(tmp_path, LoggingConfig()).start("active")
    save(log)
    image = log.path.with_name(log.path.stem + "-stop.png")
    code = """import sys
from pathlib import Path
from e7auto.run_logging import RunLogManager
from e7auto.config import LoggingConfig
log = RunLogManager(Path(sys.argv[1]), LoggingConfig(keep_runs=1)).start('child')
log.close()
"""
    try:
        subprocess.run([sys.executable, "-B", "-c", code, str(tmp_path)], check=True)
        assert image.exists() and log.path.exists()
    finally:
        log.close()


def test_symlink_artifacts_and_leases_are_not_followed(tmp_path, monkeypatch):
    old = artifacts(tmp_path)
    is_symlink = Path.is_symlink
    # Exercise the rejection branch even on hosts lacking symlink-creation privileges.
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == old[2] or is_symlink(path))
    log = RunLogManager(tmp_path, LoggingConfig()).start("new")
    log.close()
    assert old[2].exists()
    assert not old[0].exists()
