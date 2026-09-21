"""Reproducible video-level retrieval evaluation over human-authored intervals.

Run ``python -m replay_studio.evaluation --manifest manifest.json --base artifacts
--queries queries.json --output evaluation.json``. No model produces the gold labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import time
from pathlib import Path
from typing import Any

from replay_studio.media import _write_json
from replay_studio.retrieval import MODES, search


def interval_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    start_a, end_a, start_b, end_b = (float(a["start"]), float(a["end"]), float(b["start"]), float(b["end"]))
    if (
        not all(math.isfinite(v) for v in (start_a, end_a, start_b, end_b))
        or end_a <= start_a
        or end_b <= start_b
    ):
        raise ValueError("Evaluation intervals must be finite and increasing")
    intersection = max(0, min(end_a, end_b) - max(start_a, start_b))
    return intersection / (max(end_a, end_b) - min(start_a, start_b))


def validate_cases(cases: list[dict[str, Any]]) -> None:
    if not isinstance(cases, list) or not cases:
        raise ValueError("Evaluation requires at least one labelled query")
    seen = set()
    group_splits: dict[str, str] = {}
    for case in cases:
        ident = case.get("id")
        if not isinstance(ident, str) or not ident or ident in seen:
            raise ValueError("Evaluation case IDs must be nonempty and unique")
        seen.add(ident)
        if not isinstance(case.get("query"), str) or not case["query"].strip():
            raise ValueError("Each evaluation case needs a query")
        intervals = case.get("intervals", [])
        if not isinstance(intervals, list):
            raise ValueError("Gold intervals must be a list")
        for interval in intervals:
            interval_iou(interval, interval)
        answerable = case.get("answerable", bool(intervals))
        if bool(intervals) != answerable:
            raise ValueError("Answerability must agree with the presence of gold intervals")
        group = str(case.get("group", case.get("asset_id", "default")))
        split = str(case.get("split", "test"))
        if group in group_splits and group_splits[group] != split:
            raise ValueError("A source group appears in multiple data splits")
        group_splits[group] = split


def _aggregate(rows: list[dict[str, Any]], ks: tuple[int, ...]) -> dict[str, Any]:
    positive = [r for r in rows if r["answerable"]]
    negative = [r for r in rows if not r["answerable"]]
    return {
        "queries": len(rows),
        "answerable_queries": len(positive),
        "unanswerable_queries": len(negative),
        "recall_at_k": {
            str(k): sum(r["recall_at_k"][str(k)] for r in positive) / len(positive) if positive else None
            for k in ks
        },
        "mean_top1_iou": sum(r["top1_iou"] for r in positive) / len(positive) if positive else None,
        "false_positive_rate": sum(bool(r["hits"]) for r in negative) / len(negative) if negative else None,
        "correct_abstention_rate": sum(not r["hits"] for r in negative) / len(negative) if negative else None,
    }


def evaluate(
    cases: list[dict[str, Any]],
    manifest: dict[str, Any],
    base: Path,
    *,
    modes: tuple[str, ...] = MODES,
    ks: tuple[int, ...] = (1, 3, 5),
    iou_threshold: float = 0.3,
) -> dict[str, Any]:
    validate_cases(cases)
    if not ks or any(not isinstance(k, int) or not 1 <= k <= 30 for k in ks):
        raise ValueError("Recall ranks must be integers in [1,30]")
    if not 0 < iou_threshold <= 1 or not math.isfinite(iou_threshold):
        raise ValueError("IoU threshold must be in (0,1]")
    if not modes or any(mode not in MODES for mode in modes):
        raise ValueError("Choose supported retrieval modes")
    asset_ids = {case.get("asset_id") for case in cases if case.get("asset_id")}
    if len(asset_ids) > 1:
        raise ValueError("This runner evaluates one manifest at a time; do not mix assets")
    canonical = json.dumps(cases, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    reports = {}
    for mode in modes:
        rows = []
        for case in cases:
            started = time.monotonic()
            hits = search(case["query"], manifest, base, mode=mode, limit=max(ks))
            elapsed = time.monotonic() - started
            gold = case.get("intervals", [])
            scores = [max((interval_iou(hit, interval) for interval in gold), default=0) for hit in hits]
            rows.append(
                {
                    "id": case["id"],
                    "query": case["query"],
                    "group": str(case.get("group", case.get("asset_id", "default"))),
                    "type": str(case.get("type", "unspecified")),
                    "answerable": bool(gold),
                    "hits": [
                        {
                            "id": h["id"],
                            "start": h["start"],
                            "end": h["end"],
                            "modalities": h["modalities"],
                            "uncertain": h["uncertain"],
                        }
                        for h in hits
                    ],
                    "top1_iou": scores[0] if scores else 0,
                    "recall_at_k": {str(k): int(any(s >= iou_threshold for s in scores[:k])) for k in ks},
                    "elapsed_seconds": round(elapsed, 6),
                }
            )
        groups = sorted({r["group"] for r in rows})
        by_group = {group: _aggregate([r for r in rows if r["group"] == group], ks) for group in groups}
        per_video = {}
        for k in ks:
            values = [
                stats["recall_at_k"][str(k)]
                for stats in by_group.values()
                if stats["recall_at_k"][str(k)] is not None
            ]
            per_video[str(k)] = sum(values) / len(values) if values else None
        reports[mode] = {
            "micro": _aggregate(rows, ks),
            "video_macro_recall_at_k": per_video,
            "by_group": by_group,
            "by_type": {
                typ: _aggregate([r for r in rows if r["type"] == typ], ks)
                for typ in sorted({r["type"] for r in rows})
            },
            "cases": rows,
        }
    return {
        "schema_version": 1,
        "dataset_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        "manifest_sha256": hashlib.sha256(
            json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "iou_threshold": iou_threshold,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "reports": reports,
        "limitations": [
            "Gold labels are supplied by the evaluator, never inferred by this runner.",
            "One manifest per run; combine video-level results across independent videos.",
            "Measured latency includes cold model loading unless the caller externally warmed it.",
            "A small scripted fixture measures pipeline mechanics, not real-user retrieval quality.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    args = parser.parse_args()
    cases = json.loads(args.queries.read_text(encoding="utf-8"))
    if isinstance(cases, dict):
        cases = cases["cases"]
    result = evaluate(
        cases, json.loads(args.manifest.read_text(encoding="utf-8")), args.base, modes=tuple(args.modes)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(args.output, result)
    print(json.dumps({mode: data["micro"] for mode, data in result["reports"].items()}, indent=2))


if __name__ == "__main__":
    main()
