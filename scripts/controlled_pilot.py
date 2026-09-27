"""Annotation and CPU evaluation helpers for the preregistered browser pilot.

Run annotation and inspect the review sheets before committing reference labels.
Only the separate evaluate subcommand imports model/retrieval code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts" / "controlled-pilot-v1"


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def visibility_windows(
    samples: list[dict[str, Any]], predicate: str, duration: float
) -> list[dict[str, float]]:
    """Include every positive run; boundaries halfway between adjacent observations.

    A final positive observation extends to the recording end. Sampling gaps are
    rejected rather than silently inventing continuous visibility.
    """
    if not samples or duration <= 0:
        raise ValueError("Nonempty samples and positive duration required")
    times = [float(row["time"]) for row in samples]
    if times[0] < 0 or times[-1] > duration + 0.25:
        raise ValueError("Samples are outside the declared recording clock uncertainty")
    if any(not 0 < b - a <= 0.5 for a, b in zip(times, times[1:], strict=False)):
        raise ValueError("Non-monotonic or missing DOM samples need manual review")
    windows = []
    start = None
    for i, row in enumerate(samples):
        active = bool(row["states"][predicate])
        boundary = times[i] if i == 0 else (times[i - 1] + times[i]) / 2
        if active and start is None:
            start = max(0, boundary)
        elif not active and start is not None:
            windows.append({"start": round(start, 3), "end": round(min(duration, boundary), 3)})
            start = None
    if start is not None:
        windows.append({"start": round(start, 3), "end": round(duration, 3)})
    return windows


def evidence_hit(hits: list[dict[str, Any]], intervals: list[dict[str, float]], k: int) -> bool:
    return any(
        interval["start"] <= float(evidence["time"]) <= interval["end"]
        for hit in hits[:k]
        for evidence in hit.get("evidence", [])
        for interval in intervals
    )


def boundary_error(hit: dict[str, Any] | None, intervals: list[dict[str, float]]) -> dict[str, float] | None:
    if hit is None or not intervals:
        return None
    nearest = min(intervals, key=lambda w: abs(hit["start"] - w["start"]) + abs(hit["end"] - w["end"]))
    return {
        "start_absolute_seconds": abs(hit["start"] - nearest["start"]),
        "end_absolute_seconds": abs(hit["end"] - nearest["end"]),
    }


def review_sheet(source: Path, cases: list[dict[str, Any]], out: Path, duration: float) -> list[float]:
    """Real decoded video frames, not model outputs or synthetic scenes."""
    from PIL import Image, ImageDraw

    stamps = {0.5, max(0.5, duration - 0.5)}
    stamps.update(float(t) for t in range(5, int(duration), 10))
    for case in cases:
        for window in case["intervals"]:
            stamps.update(
                {
                    max(0.1, window["start"] - 0.4),
                    window["start"] + 0.4,
                    (window["start"] + window["end"]) / 2,
                    min(duration - 0.1, window["end"] - 0.4),
                    min(duration - 0.1, window["end"] + 0.4),
                }
            )
    ordered = sorted({round(t, 2) for t in stamps if 0 <= t < duration})
    raw = ROOT / "data" / "controlled-pilot-v1" / source.parent.name / "review-frames"
    raw.mkdir(parents=True, exist_ok=True)
    for page_index in range(0, len(ordered), 12):
        subset = ordered[page_index : page_index + 12]
        canvas = Image.new("RGB", (1536, 4 * 380), "#e9eef5")
        draw = ImageDraw.Draw(canvas)
        for i, stamp in enumerate(subset):
            frame_path = raw / f"{stamp:.2f}.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    str(stamp),
                    "-i",
                    str(source),
                    "-frames:v",
                    "1",
                    str(frame_path),
                ],
                check=True,
            )
            with Image.open(frame_path) as frame:
                frame.thumbnail((512, 360))
                x, y = (i % 3) * 512, (i // 3) * 380
                canvas.paste(frame, (x, y + 20))
                draw.text((x + 8, y + 4), f"Source video t={stamp:.2f}s", fill="black")
        canvas.save(out / f"review-sheet-{page_index // 12 + 1}.jpg", quality=88)
    return ordered


def annotate() -> None:
    protocol = read(ARTIFACTS / "protocol.json")
    for task in protocol["tasks"]:
        out = ARTIFACTS / task["asset_id"]
        if (out / "queries.json").exists() and read(out / "annotation-receipt.json")[
            "visual_review_status"
        ] != "pending":
            raise ValueError("Refusing to overwrite frozen reference labels")
        source = read(out / "source.json")
        duration = float(source["ffprobe"]["format"]["duration"])
        alignment = read(ARTIFACTS / "clock-alignment.json")
        offset = alignment[task["asset_id"]]["subtract_from_dom_seconds"]
        samples = [{**row, "time": row["time"] - offset} for row in read(out / "events.json")["samples"]]
        cases = []
        for query in task["queries"]:
            intervals = visibility_windows(samples, query["predicate"], duration)
            if bool(intervals) != query["answerable"]:
                raise ValueError(f"Task execution did not satisfy frozen answerability: {query['id']}")
            cases.append(
                {
                    **query,
                    "asset_id": task["asset_id"],
                    "group": protocol["source_group"],
                    "split": protocol["split"],
                    "type": "screen_only",
                    "intervals": intervals,
                }
            )
        stamps = review_sheet(out / "source.webm", cases, out, duration)
        write(out / "queries.json", cases)
        write(
            out / "annotation-receipt.json",
            {
                "protocol_sha256": sha(ARTIFACTS / "protocol.json"),
                "source_sha256": sha(out / "source.webm"),
                "events_sha256": sha(out / "events.json"),
                "queries_sha256": sha(out / "queries.json"),
                "review_frame_times": stamps,
                "visual_review_status": "pending",
                "author": "AI; DOM-state references awaiting direct frame verification, not human/expert gold",
                "timing_uncertainty_seconds": 0.25,
            },
        )
        print(task["asset_id"], [(c["id"], c["intervals"]) for c in cases], flush=True)


def evaluate_pilot(results_dir: Path | None = None) -> None:
    # Set before importing any numerical/model runtime. No GPU or online weights.
    for key, value in {
        "REPLAY_MODEL_DEVICE": "cpu",
        "CUDA_VISIBLE_DEVICES": "",
        "OMP_NUM_THREADS": "2",
        "MKL_NUM_THREADS": "2",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "REPLAY_VLM_MODEL": "",
        "REPLAY_PLANNER_MODEL": "",
        "REPLAY_VISUAL_MIN_SIMILARITY": "0.27",
        "REPLAY_VISUAL_MODEL": "openai/clip-vit-base-patch32",
    }.items():
        os.environ[key] = value
    import torch

    from replay_studio.analysis import ocr, transcribe, visual
    from replay_studio.evaluation import evaluate
    from replay_studio.media import prepare
    from replay_studio.retrieval import MODES, search

    torch.set_num_threads(2)
    results_dir = results_dir or ARTIFACTS
    protocol = read(ARTIFACTS / "protocol.json")
    summary = {
        "protocol_sha256": sha(ARTIFACTS / "protocol.json"),
        "device": "cpu",
        "gpu_used": False,
        "environment": {p: version(p) for p in ["rapidocr", "onnxruntime", "torch", "transformers", "numpy"]},
        "python": platform.python_version(),
        "platform": platform.platform(),
        "assets": {},
    }
    for task in protocol["tasks"]:
        source_dir = ARTIFACTS / task["asset_id"]
        out = results_dir / task["asset_id"]
        receipt = read(source_dir / "annotation-receipt.json")
        if receipt["visual_review_status"] != "verified_before_model_evaluation":
            raise ValueError("Direct frame verification must precede evaluation")
        for filename, field in [("protocol.json", "protocol_sha256")]:
            if sha(ARTIFACTS / filename) != receipt[field]:
                raise ValueError(f"Frozen protocol receipt mismatch: {filename}")
        for filename, field in [
            ("source.webm", "source_sha256"),
            ("events.json", "events_sha256"),
            ("queries.json", "queries_sha256"),
        ]:
            if sha(source_dir / filename) != receipt[field]:
                raise ValueError(f"Reference receipt mismatch: {filename}")
        if (out / "evaluation.json").exists():
            raise ValueError("Refusing to overwrite measured results")
        base = ROOT / "data" / "controlled-pilot-v1" / task["asset_id"] / "index"
        started = time.monotonic()
        prepared = prepare(source_dir / "source.webm", base)
        print(task["asset_id"], "prepared", len(prepared["frames"]), flush=True)
        asr_result = transcribe(None, base)
        ocr_result = ocr(prepared["frames"], base, base)
        print(task["asset_id"], "ocr", ocr_result["status"], flush=True)
        visual_result = visual(prepared["frames"], base, base)
        print(task["asset_id"], "visual", visual_result["status"], flush=True)
        if ocr_result["status"] != "complete" or visual_result["status"] != "complete":
            raise RuntimeError("A required real model is unavailable; do not report a complete pilot")
        index_seconds = time.monotonic() - started
        manifest = {"prepare": prepared, "asr": asr_result, "ocr": ocr_result, "visual": visual_result}
        write(base / "manifest.json", manifest)
        write(out / "manifest.json", manifest)
        shutil.copyfile(base / "visual.npz", out / "visual.npz")
        cases = read(source_dir / "queries.json")
        result = evaluate(cases, manifest, base)
        # Preserve the first measured evaluation; this second call only gathers
        # deterministic evidence details omitted by the generic evaluator.
        diagnostics = {}
        for mode in MODES:
            rows = []
            for case in cases:
                hits = search(case["query"], manifest, base, mode=mode, limit=5)
                rows.append(
                    {
                        "id": case["id"],
                        "answerable": case["answerable"],
                        "evidence_time_hit_at_k": {
                            str(k): int(evidence_hit(hits, case["intervals"], k)) for k in (1, 3, 5)
                        },
                        "top1_boundary_error": boundary_error(hits[0] if hits else None, case["intervals"]),
                        "hits": hits,
                    }
                )
            diagnostics[mode] = rows
        write(out / "evaluation.json", result)
        write(out / "diagnostics.json", diagnostics)
        summary["assets"][task["asset_id"]] = {
            "duration_seconds": prepared["duration"],
            "sampled_frames": len(prepared["frames"]),
            "index_seconds": round(index_seconds, 3),
            "ocr_seconds": ocr_result["elapsed_seconds"],
            "clip_seconds": visual_result["elapsed_seconds"],
            "visual_model_revision": visual_result.get("model_revision"),
            "source_sha256": sha(source_dir / "source.webm"),
            "queries_sha256": sha(source_dir / "queries.json"),
            "clock_alignment_sha256": sha(ARTIFACTS / "clock-alignment.json"),
            "modes": {mode: report["micro"] for mode, report in result["reports"].items()},
            "evidence_time_hit_at_k": {
                mode: {
                    str(k): sum(r["evidence_time_hit_at_k"][str(k)] for r in rows if r["answerable"]) / 4
                    for k in (1, 3, 5)
                }
                for mode, rows in diagnostics.items()
            },
        }
        write(results_dir / "summary.json", summary)
        print(task["asset_id"], summary["assets"][task["asset_id"]], flush=True)


def summarize_results(results_dir: Path) -> None:
    """Pool recorded cases without running models or treating clips as independent groups."""
    protocol = read(ARTIFACTS / "protocol.json")
    reports = [read(results_dir / task["asset_id"] / "evaluation.json") for task in protocol["tasks"]]
    diagnostics = [read(results_dir / task["asset_id"] / "diagnostics.json") for task in protocol["tasks"]]
    pooled: dict[str, Any] = {
        "source_group": protocol["source_group"],
        "independent_source_groups": 1,
        "confidence_intervals": None,
        "modes": {},
    }
    for mode in ("speech", "speech_ocr", "fusion"):
        cases = [case for report in reports for case in report["reports"][mode]["cases"]]
        positive = [case for case in cases if case["answerable"]]
        negative = [case for case in cases if not case["answerable"]]
        diagnostic_rows = [case for report in diagnostics for case in report[mode] if case["answerable"]]
        assert len(diagnostic_rows) == len(positive)
        boundaries = [
            row["top1_boundary_error"] for row in diagnostic_rows if row["top1_boundary_error"] is not None
        ]
        pooled["modes"][mode] = {
            "answerable_queries": len(positive),
            "unanswerable_queries": len(negative),
            "interval_recall_hits_at_k": {
                str(k): sum(row["recall_at_k"][str(k)] for row in positive) for k in (1, 3, 5)
            },
            "mean_top1_iou": sum(row["top1_iou"] for row in positive) / len(positive),
            "false_positives": sum(bool(row["hits"]) for row in negative),
            "evidence_time_hits_at_k": {
                str(k): sum(row["evidence_time_hit_at_k"][str(k)] for row in diagnostic_rows)
                for k in (1, 3, 5)
            },
            "top1_boundary_errors": {
                "queries_with_hits": len(boundaries),
                "missing_no_hit": len(positive) - len(boundaries),
                "mean_start_absolute_seconds": sum(row["start_absolute_seconds"] for row in boundaries)
                / len(boundaries)
                if boundaries
                else None,
                "mean_end_absolute_seconds": sum(row["end_absolute_seconds"] for row in boundaries)
                / len(boundaries)
                if boundaries
                else None,
            },
        }
    write(results_dir / "pooled.json", pooled)
    print(json.dumps(pooled, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("annotate", "evaluate", "summarize"))
    parser.add_argument(
        "--results-dir",
        type=Path,
        help="Separate result location for replaying committed sources; existing results are never overwritten",
    )
    args = parser.parse_args()
    if args.command == "annotate":
        annotate()
    elif args.command == "summarize":
        summarize_results(args.results_dir or ARTIFACTS)
    else:
        evaluate_pilot(args.results_dir)


if __name__ == "__main__":
    main()
