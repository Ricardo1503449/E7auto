from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import e7auto.ui.worker as worker_module
import e7auto.bootstrap as bootstrap_module
from e7auto.core.domain import StopReason
from e7auto.logging.run import RunLogger
from tests.helpers import make_config


@pytest.mark.parametrize("writer_fails", [False, True])
def test_worker_setup_failure_records_missing_cache_without_starting_capture(tmp_path, monkeypatch, writer_fails):
    def fail_templates(*_): raise ValueError("template setup failure")
    def forbidden_capture(*args, **kwargs): raise AssertionError("Capture must not start")
    monkeypatch.setitem(sys.modules, "e7auto.platform.wgc_capture", SimpleNamespace(WindowsGraphicsCaptureService=forbidden_capture))
    monkeypatch.setattr(bootstrap_module, "TemplateRepository", fail_templates)
    if writer_fails:
        def fail_writer(*args, **kwargs): raise OSError("diagnostic writer failed")
        monkeypatch.setattr(RunLogger, "save_stop_snapshot", fail_writer)
    worker = worker_module.AutomationWorker(make_config(), 0, False, tmp_path, None)
    final = []
    worker.finished.connect(final.append)
    worker.run()
    assert len(final) == 1 and final[0].stop_reason is StopReason.INTERNAL_ERROR
    text = next((tmp_path / "logs").glob("*.log")).read_text(encoding="utf-8")
    assert "event=worker_setup_failed" in text
    assert "template setup failure" in text
    assert "event=stop_snapshot" in text
    assert ("diagnostic writer failed" if writer_fails else "detail=no_cached_frame") in text
    assert not list((tmp_path / "logs").glob("*.png"))
