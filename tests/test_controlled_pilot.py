"""Reference-window and diagnostic behavior, independent of model predictions."""

import hashlib
import importlib.util
import json
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


def test_committed_reference_receipts_match_exact_bytes_and_frozen_queries():
    root = Path(__file__).parents[1] / "artifacts" / "controlled-pilot-v1"
    protocol = json.loads((root / "protocol.json").read_text(encoding="utf-8"))
    for task in protocol["tasks"]:
        asset = root / task["asset_id"]
        receipt = json.loads((asset / "annotation-receipt.json").read_text(encoding="utf-8"))
        for path, field in [
            (root / "protocol.json", "protocol_sha256"),
            (asset / "source.webm", "source_sha256"),
            (asset / "events.json", "events_sha256"),
            (asset / "queries.json", "queries_sha256"),
        ]:
            assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt[field]
        cases = json.loads((asset / "queries.json").read_text(encoding="utf-8"))
        assert len(cases) == len(task["queries"])
        for case, frozen in zip(cases, task["queries"], strict=True):
            assert {key: case[key] for key in frozen} == frozen
            assert bool(case["intervals"]) == frozen["answerable"]
