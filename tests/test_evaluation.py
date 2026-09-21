from __future__ import annotations

from pathlib import Path

import pytest

from replay_studio.evaluation import evaluate, interval_iou, validate_cases


def test_iou_known_overlap_and_invalid_intervals() -> None:
    assert interval_iou({"start": 0, "end": 4}, {"start": 2, "end": 6}) == pytest.approx(1 / 3)
    with pytest.raises(ValueError):
        interval_iou({"start": 0, "end": float("nan")}, {"start": 0, "end": 1})


def test_source_groups_cannot_leak_across_splits() -> None:
    cases = [
        {"id": "a", "query": "x", "intervals": [], "group": "video-1", "split": "train"},
        {"id": "b", "query": "y", "intervals": [], "group": "video-1", "split": "test"},
    ]
    with pytest.raises(ValueError, match="multiple data splits"):
        validate_cases(cases)


def test_evaluation_records_negatives_and_reproducible_identity(tmp_path: Path) -> None:
    manifest = {
        "prepare": {"duration": 10},
        "asr": {"segments": [{"start": 2, "end": 4, "text": "Database failure"}]},
    }
    cases = [
        {"id": "positive", "query": "database", "intervals": [{"start": 2, "end": 4}], "type": "speech"},
        {"id": "negative", "query": "elephants", "intervals": [], "type": "absent"},
    ]
    first = evaluate(cases, manifest, tmp_path, modes=("speech",))
    second = evaluate(cases, manifest, tmp_path, modes=("speech",))
    assert first["dataset_sha256"] == second["dataset_sha256"]
    stats = first["reports"]["speech"]["micro"]
    assert stats["recall_at_k"]["1"] == 1
    assert stats["correct_abstention_rate"] == 1
    assert stats["false_positive_rate"] == 0
    assert stats["mean_top1_iou"] == 1


def test_cannot_report_unanswerable_without_matching_gold() -> None:
    with pytest.raises(ValueError, match="Answerability"):
        validate_cases([{"id": "x", "query": "x", "intervals": [], "answerable": True}])
