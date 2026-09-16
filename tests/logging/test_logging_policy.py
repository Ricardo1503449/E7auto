from __future__ import annotations
import os
from pathlib import Path
from e7auto.run_logging import RunLogManager
from e7auto.config import LoggingConfig

def test_text_logger_is_utf8_and_file_count_is_bounded(tmp_path: Path) -> None:
    manager = RunLogManager(tmp_path, LoggingConfig(30, 2))
    for run_id in ("one", "two", "three"):
        logger = manager.start(run_id)
        logger.event("message", text="中文", roi="1,2,3,4")
        logger.close()
    files = list(tmp_path.glob("run-*.log"))
    assert len(files) <= 2
    assert any("中文" in path.read_text(encoding="utf-8") for path in files)
    assert not list(tmp_path.glob("*.png"))


def test_text_logger_prunes_numbered_rotations_but_not_lookalikes(tmp_path: Path) -> None:
    rotations = [tmp_path / "run-old.log.1", tmp_path / "run-old.log.2"]
    lookalike = tmp_path / "run-old.log.backup"
    for path in (*rotations, lookalike):
        path.write_text("old", encoding="utf-8")
        os.utime(path, (1_600_000_000, 1_600_000_000))

    logger = RunLogManager(tmp_path, LoggingConfig(1, 5)).start("current")
    logger.close()

    assert all(not path.exists() for path in rotations)
    assert lookalike.exists()


def test_logger_records_all_diagnostic_events(tmp_path: Path) -> None:
    manager = RunLogManager(tmp_path, LoggingConfig(30, 2))
    logger = manager.start("diagnostics")
    logger.event("recognition", object="shop", detected=False)
    logger.event("recognition", object="shop", detected=True, stable=1, confidence="0.9")
    logger.event("recognition", object="shop", detected=True, stable=3, confidence="0.99")
    logger.event("inventory_scan", screen="top", targets=0, stable=1)
    logger.event("inventory_scan", screen="top", targets=1, stable=3)
    logger.event("sky_stone_observation", stage="before_refresh", value=100, stable=1)
    logger.event(
        "input_failed",
        action="confirm_refresh",
        logical_x=1485,
        logical_y=925,
        screen_x=1500,
        screen_y=950,
        error="denied",
    )
    logger.event("refresh_counted", sky_stone_before=100, sky_stone_after=97)
    logger.event("run_stopped", reason="input_failure", detail="confirm_refresh denied")
    logger.close()
    text = next(tmp_path.glob("run-*.log")).read_text(encoding="utf-8")
    assert "event=recognition" in text
    assert "event=inventory_scan" in text
    assert "event=sky_stone_observation" in text
    assert "event=input_failed" in text
    assert "logical_x=" in text
    assert "screen_x=" in text
    assert "event=refresh_counted" in text
    assert "event=run_stopped" in text
