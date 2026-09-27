"""Reference-window and diagnostic behavior, independent of model predictions."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "controlled_pilot", Path(__file__).parents[1] / "scripts" / "controlled_pilot.py"
)
assert spec and spec.loader
pilot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pilot)


def test_all_visibility_recurrences_are_kept_and_last_extends_to_end():
    samples = [
        {"time": i / 10, "states": {"visible": active}}
        for i, active in enumerate([False, True, True, False, True, True])
    ]
    assert pilot.visibility_windows(samples, "visible", 0.6) == [
        {"start": 0.05, "end": 0.25},
        {"start": 0.35, "end": 0.6},
    ]


def test_missing_dom_samples_are_not_invented_as_continuous_visibility():
    with pytest.raises(ValueError, match="missing DOM"):
        pilot.visibility_windows([{"time": t, "states": {"visible": True}} for t in (0, 1)], "visible", 2)


def test_evidence_time_hit_is_distinct_from_interval_overlap():
    hits = [{"start": 1, "end": 5, "evidence": [{"time": 1.5}]}]
    assert not pilot.evidence_hit(hits, [{"start": 3, "end": 10}], 1)
    assert pilot.evidence_hit(hits, [{"start": 1, "end": 10}], 1)


def test_missing_hits_have_no_fake_zero_boundary_error():
    intervals = [{"start": 3, "end": 10}, {"start": 20, "end": 30}]
    assert pilot.boundary_error(None, intervals) is None
    assert pilot.boundary_error({"start": 21, "end": 28}, intervals) == {
        "start_absolute_seconds": 1,
        "end_absolute_seconds": 2,
    }
