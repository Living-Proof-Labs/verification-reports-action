# SPDX-License-Identifier: Apache-2.0
from compare_integrity import different, view


def _document(percentage, finding, started="2026-11-27T00:00:00Z", version="1.9.0"):
    return {
        "evaluator": {"id": "example", "version": version, "display_name": "Run Integrity"},
        "descriptor": {"version": version, "title": "Run Integrity"},
        "timestamps": {"started_at": started, "completed_at": started},
        "integrity": {
            "run": {
                "test_system_status": "COMPLETED",
                "percentage": percentage,
                "started_at": started,
            },
            "findings": [finding],
        },
    }


def test_timestamps_and_version_stamps_are_not_a_difference():
    left = _document(80, {"id": "gap"}, started="2026-11-26T00:00:00Z", version="1.8.6")
    right = _document(80, {"id": "gap"}, started="2026-11-27T00:00:00Z", version="1.9.0")
    assert view(left) == view(right)
    assert different(left, right) is False


def test_a_finding_or_percentage_change_is_a_difference():
    base = _document(80, {"id": "gap"})
    assert different(base, _document(70, {"id": "gap"})) is True
    assert different(base, _document(80, {"id": "other"})) is True
