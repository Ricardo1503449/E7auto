from __future__ import annotations

from scripts.validate_insufficient_funds import terminal_criteria


def test_terminal_criteria_reports_compliant_no_screenshot_evidence_as_passed() -> None:
    criteria = terminal_criteria(stable=5, required=3)

    assert criteria["stable_terminal_detection"] is True
    assert criteria["no_screenshots_persisted"] is True
    assert "screenshots_persisted" not in criteria
    assert all(criteria.values())


def test_terminal_criteria_still_rejects_too_few_positive_frames() -> None:
    criteria = terminal_criteria(stable=2, required=3)

    assert criteria["stable_terminal_detection"] is False
    assert not all(criteria.values())
