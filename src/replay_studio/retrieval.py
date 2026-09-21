"""Auditable temporal retrieval and an explicitly deterministic edit planner."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from replay_studio.media import safe_path, validate_clips

MODES = ("speech", "speech_ocr", "fusion")
log = logging.getLogger(__name__)
_STOP = set(
    "a an and are as at be before by can clip find for from give happened in into is it me of on or please section show that the then this to video was were what when where which with moment moments first last after footage seconds second".split()
)
_ALIASES = {
    "failed": "error",
    "failure": "error",
    "fail": "error",
    "exception": "error",
    "traceback": "error",
    "errors": "error",
    "failing": "error",
    "errored": "error",
    "报错": "error",
    "错误": "error",
    "successfully": "success",
    "successful": "success",
    "succeeded": "success",
    "passed": "success",
    "成功": "success",
    "fixed": "fix",
    "fixing": "fix",
    "repair": "fix",
    "修复": "fix",
    "configuration": "config",
    "settings": "config",
    "配置": "config",
}


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", text.lower())
    result = []
    for word in words:
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            result.extend(_ALIASES.get(word[i : i + 2], word[i : i + 2]) for i in range(len(word) - 1))
            if len(word) == 1:
                result.append(word)
        elif word not in _STOP:
            result.append(_ALIASES.get(word, word))
    return result


def _id(modality: str, start: float, end: float, text: str) -> str:
    value = f"{modality}\0{start:.6f}\0{end:.6f}\0{text}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _duration(manifest: dict[str, Any]) -> float:
    duration = float(manifest.get("prepare", {}).get("duration", 0))
    if not math.isfinite(duration) or not 0 < duration <= 1800.1:
        raise ValueError("A completed manifest with a finite video duration is required")
    return duration


def manifest_evidence_ids(manifest: dict[str, Any]) -> set[str]:
    """Enumerate source IDs without loading models or trusting client search results.

    Fused hits retain their first constituent's ID, so this union also covers
    fusion results. The committed manifest is the authority; this helper does
    not verify that its referenced files are currently materialized on disk.
    """
    duration = _duration(manifest)
    identifiers: set[str] = set()

    def add_segments(segments: list[dict[str, Any]], modality: str) -> None:
        for segment in segments[:20000]:
            try:
                start, end = float(segment["start"]), float(segment["end"])
                text = str(segment["text"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if (
                text
                and math.isfinite(start)
                and math.isfinite(end)
                and 0 <= start < end <= duration + 0.1
                and (segment.get("frame") is None or isinstance(segment["frame"], str))
            ):
                identifiers.add(_id(modality, start, min(end, duration), text))

    add_segments(manifest.get("asr", {}).get("segments", []), "speech")
    add_segments(manifest.get("ocr", {}).get("segments", []), "screen")
    visual = manifest.get("visual", {})
    if visual.get("status") == "complete" and visual.get("index"):
        for frame in manifest.get("prepare", {}).get("frames", [])[:900]:
            try:
                stamp, path = float(frame["time"]), frame["path"]
            except (KeyError, TypeError, ValueError):
                continue
            if math.isfinite(stamp) and 0 <= stamp < duration and isinstance(path, str):
                identifiers.add(_id("visual", stamp, stamp, path))
    vlm = visual.get("vlm", {})
    if vlm.get("status") == "complete":
        for observation in vlm.get("observations", [])[:12]:
            try:
                stamp = float(observation["time"])
                add_segments(
                    [
                        {
                            "start": stamp,
                            "end": min(duration, stamp + 1 / 30),
                            "text": observation["text"],
                            "frame": observation["frame"],
                        }
                    ],
                    "visual",
                )
            except (KeyError, TypeError, ValueError):
                continue
    return identifiers


def _lexical(
    query: str, segments: list[dict[str, Any]], modality: str, duration: float, base: Path
) -> list[dict[str, Any]]:
    query_tokens = set(tokens(query))
    if not query_tokens:
        return []
    documents = []
    for segment in segments[:20000]:
        try:
            start, end = float(segment["start"]), float(segment["end"])
            text = str(segment["text"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if (
            not text
            or not math.isfinite(start)
            or not math.isfinite(end)
            or not 0 <= start < end <= duration + 0.1
        ):
            continue
        frame = segment.get("frame")
        if frame is not None:
            if not isinstance(frame, str) or not safe_path(base, frame).is_file():
                continue
        counts = Counter(tokens(text))
        documents.append((segment, start, min(end, duration), text, frame, counts))
    if not documents:
        return []
    frequencies: Counter[str] = Counter()
    for *_, counts in documents:
        frequencies.update(counts.keys())
    average = sum(sum(row[-1].values()) for row in documents) / len(documents)
    result = []
    for _segment, start, end, text, frame, counts in documents:
        overlap = query_tokens.intersection(counts)
        if not overlap:
            continue
        score = 0.0
        length = sum(counts.values())
        for term in overlap:
            inverse = math.log(1 + (len(documents) - frequencies[term] + 0.5) / (frequencies[term] + 0.5))
            tf = counts[term]
            score += inverse * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / max(average, 1)))
        # Sparse exact terms are evidence of lexical relevance, not entailment.
        score *= 0.5 + 0.5 * len(overlap) / len(query_tokens)
        result.append(
            {
                "id": _id(modality, start, end, text),
                "start": max(0, start - (0.5 if modality == "screen" else 0)),
                "end": min(duration, max(end, start + (2.0 if modality == "screen" else 0.1))),
                "score": score,
                "text": text,
                "modalities": [modality],
                "frame": frame,
                "evidence": [
                    {
                        "modality": modality,
                        "text": text,
                        "time": start,
                        "end": end,
                        "source": "asr" if modality == "speech" else "ocr",
                    }
                ],
                "uncertain": len(overlap) < len(query_tokens),
                "_rank_score": score,
            }
        )
    return sorted(result, key=lambda hit: (-hit["score"], hit["start"], hit["id"]))


def _visual(query: str, manifest: dict[str, Any], base: Path, duration: float) -> list[dict[str, Any]]:
    visual = manifest.get("visual", {})
    if visual.get("status") != "complete" or not visual.get("index"):
        return []
    import numpy as np

    from replay_studio.analysis import text_embedding

    path = safe_path(base, str(visual["index"]))
    if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Visual index is missing or exceeds its size limit")
    with np.load(path, allow_pickle=False) as index:
        embeddings = np.asarray(index["embeddings"], dtype=np.float32)
        times = np.asarray(index["times"], dtype=np.float64)
        frames = np.asarray(index["frames"])
        model_name = str(index["model"].item())
        if model_name != visual.get("model"):
            raise ValueError("Visual model identity does not match the manifest")
        if embeddings.ndim != 2 or embeddings.shape[0] > 900 or embeddings.shape[1] > 4096:
            raise ValueError("Invalid visual index dimensions")
        if len(times) != len(embeddings) or len(frames) != len(embeddings):
            raise ValueError("Visual index arrays have inconsistent lengths")
        if not np.isfinite(embeddings).all() or not np.isfinite(times).all():
            raise ValueError("Visual index contains non-finite values")
        revision = str(index["revision"].item()) if "revision" in index else None
        if revision and visual.get("model_revision") and revision != visual["model_revision"]:
            raise ValueError("Visual revision does not match the manifest")
        vector = np.asarray(text_embedding(query, model_name, revision or None), dtype=np.float32)
        if vector.shape != (embeddings.shape[1],) or not np.isfinite(vector).all():
            raise ValueError("Query embedding does not match the visual index")
        similarities = embeddings @ vector
        floor = float(os.environ.get("REPLAY_VISUAL_MIN_SIMILARITY", "0.27"))
        if not math.isfinite(floor) or not -1 <= floor <= 1:
            raise ValueError("Visual similarity floor must be within [-1,1]")
        result = []
        for i in np.argsort(-similarities, kind="stable")[:32]:
            stamp, score, frame = float(times[i]), float(similarities[i]), str(frames[i])
            if score < floor or not 0 <= stamp < duration or not safe_path(base, frame).is_file():
                continue
            text = f"Visual candidate at {stamp:.1f}s — inspect the frame to confirm"
            result.append(
                {
                    "id": _id("visual", stamp, stamp, frame),
                    "start": max(0, stamp - 1),
                    "end": min(duration, stamp + 2),
                    "score": score,
                    "text": text,
                    "modalities": ["visual"],
                    "frame": frame,
                    "uncertain": True,
                    "evidence": [
                        {
                            "modality": "visual",
                            "text": "CLIP image–text similarity; no verified event label",
                            "time": stamp,
                            "source": "clip",
                            "similarity": score,
                        }
                    ],
                    "_rank_score": score,
                }
            )
        return result


def _vlm_hits(query: str, manifest: dict[str, Any], base: Path, duration: float) -> list[dict[str, Any]]:
    vlm = manifest.get("visual", {}).get("vlm", {})
    if vlm.get("status") != "complete":
        return []
    segments = [
        {
            "start": observation["time"],
            "end": min(duration, observation["time"] + 1 / 30),
            "text": observation["text"],
            "frame": observation["frame"],
        }
        for observation in vlm.get("observations", [])[:12]
    ]
    hits = _lexical(query, segments, "visual", duration, base)
    for hit in hits:
        hit["text"] = "VLM interpretation: " + hit["text"]
        hit["uncertain"] = True
        hit["start"] = max(0, hit["start"] - 1)
        hit["end"] = min(duration, hit["end"] + 2)
        for evidence in hit["evidence"]:
            evidence["source"] = "vlm_unverified"
            evidence["model"] = vlm.get("model")
    return hits


def search(
    query: str, manifest: dict[str, Any], base: Path, *, mode: str = "fusion", limit: int = 8
) -> list[dict[str, Any]]:
    if mode not in MODES:
        raise ValueError(f"Unknown retrieval mode: {mode}")
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("Query must contain between 1 and 2000 characters")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 30:
        raise ValueError("Result limit must be between 1 and 30")
    duration = _duration(manifest)
    ranks = [_lexical(query, manifest.get("asr", {}).get("segments", []), "speech", duration, base)]
    if mode in ("speech_ocr", "fusion"):
        ranks.append(_lexical(query, manifest.get("ocr", {}).get("segments", []), "screen", duration, base))
    visual_warning = None
    if mode == "fusion":
        ranks.append(_vlm_hits(query, manifest, base, duration))
        try:
            ranks.append(_visual(query, manifest, base, duration))
        except (ImportError, OSError, RuntimeError) as exc:
            # A failed optional model never silently becomes synthetic matches.
            log.exception("Visual query encoding failed")
            visual_warning = (
                f"Visual search unavailable ({type(exc).__name__}); ranking uses available text evidence."
            )
    if visual_warning and not any(ranks):
        raise RuntimeError(
            "Visual search is unavailable and text retrieval returned no candidates. Retry with speech_ocr or inspect worker logs."
        )
    # Fuse signals referring to approximately the same moment. RRF avoids presenting
    # incomparable BM25/cosine numbers as calibrated confidence probabilities.
    grouped: dict[int, dict[str, Any]] = {}
    for modality_hits in ranks:
        for rank, original in enumerate(modality_hits[:120], start=1):
            hit = dict(original)
            key = int(float(hit["evidence"][0]["time"]) // 2)
            weight = 0.6 if hit["modalities"] == ["visual"] else 1.0
            contribution = weight / (60 + rank)
            if key not in grouped:
                grouped[key] = {
                    **hit,
                    "score": contribution,
                    "modalities": list(hit["modalities"]),
                    "evidence": list(hit["evidence"]),
                }
            else:
                current = grouped[key]
                current["score"] += contribution
                current["start"] = min(current["start"], hit["start"])
                current["end"] = max(current["end"], hit["end"])
                current["modalities"] = sorted(set(current["modalities"] + hit["modalities"]))
                current["evidence"].extend(hit["evidence"])
                current["frame"] = current["frame"] or hit["frame"]
                current["uncertain"] = current["uncertain"] and hit["uncertain"]
                if current["text"].startswith("Visual candidate") and hit["modalities"] != ["visual"]:
                    current["text"] = hit["text"]
    ordered = sorted(grouped.values(), key=lambda h: (-h["score"], h["start"], h["id"]))
    selected = []
    for hit in ordered:
        # Suppress repetitive adjacent screenshots, while retaining distant recurrences.
        if any(
            tokens(h["text"]) == tokens(hit["text"]) and abs(h["start"] - hit["start"]) < 8 for h in selected
        ):
            continue
        hit.pop("_rank_score", None)
        hit["score"] = round(hit["score"], 8)
        hit["score_kind"] = "reciprocal_rank_fusion"
        if visual_warning:
            hit["warnings"] = [visual_warning]
        selected.append(hit)
        if len(selected) == limit:
            break
    return selected


def parse_evidence_selection(text: str, allowed: set[str]) -> list[str]:
    """Accept the entire small JSON response, rejecting extra fields and duplicate keys."""

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        keys = [key for key, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("Planner returned duplicate JSON keys")
        return dict(pairs)

    if not isinstance(text, str) or not text.strip() or len(text) > 10000:
        raise ValueError("Planner returned an empty or oversized response")
    try:
        payload = json.loads(text, object_pairs_hook=unique_object)
    except json.JSONDecodeError as exc:
        raise ValueError("Planner returned invalid JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {"evidence_ids"}:
        raise ValueError("Planner may return only evidence_ids")
    identifiers = payload["evidence_ids"]
    if not isinstance(identifiers, list) or len(identifiers) > 8:
        raise ValueError("Planner evidence_ids must be a list of at most eight IDs")
    if any(not isinstance(identifier, str) or identifier not in allowed for identifier in identifiers):
        raise ValueError("Planner selected an unknown evidence ID")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Planner selected duplicate evidence IDs")
    return identifiers


def plan(
    query: str, hits: list[dict[str, Any]], duration: float, *, target_seconds: float = 90
) -> dict[str, Any]:
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("A bounded, nonempty editing request is required")
    if not math.isfinite(duration) or not 0 < duration <= 1800.1:
        raise ValueError("Source duration must be finite and within 30 minutes")
    if (
        isinstance(target_seconds, bool)
        or not math.isfinite(target_seconds)
        or not 1 <= target_seconds <= 600
    ):
        raise ValueError("Target duration must be between 1 and 600 seconds")
    warnings = [
        "Deterministic baseline: selects evidence chronologically; it does not verify causality or guarantee all natural-language instructions are satisfied."
    ]
    if not hits:
        return {
            "clips": [],
            "warnings": warnings + ["No supported candidates found; no footage was invented."],
            "strategy": "deterministic_evidence_timeline_v1",
        }
    valid_hits: dict[str, dict[str, Any]] = {}
    for hit in hits[:30]:
        try:
            start, end = float(hit["start"]), float(hit["end"])
        except (KeyError, TypeError, ValueError):
            continue
        identifier = hit.get("id")
        if (
            not isinstance(identifier, str)
            or not identifier
            or len(identifier) > 128
            or not math.isfinite(start)
            or not math.isfinite(end)
            or not 0 <= start < end <= duration
            or not isinstance(hit.get("evidence"), list)
            or not hit["evidence"]
        ):
            continue
        valid_hits.setdefault(identifier, hit)
    hits = list(valid_hits.values())
    strategy = "deterministic_evidence_timeline_v1"
    planner_info: dict[str, Any] = {"status": "disabled", "model": None}
    planner_name = os.environ.get("REPLAY_PLANNER_MODEL", "").strip()
    if planner_name and hits:
        from replay_studio.analysis import select_evidence

        try:
            selection = select_evidence(query, hits, target_seconds)
            identifiers = parse_evidence_selection(selection["text"], set(valid_hits))
            hits = [valid_hits[identifier] for identifier in identifiers]
            strategy = "local_llm_evidence_selection_v1"
            planner_info = {key: value for key, value in selection.items() if key != "text"}
            planner_info["status"] = "complete"
            warnings = [
                "A local model selected and ordered known evidence IDs. Source times and subtitles were copied from validated candidates; review the semantic fit."
            ]
            if not identifiers:
                planner_info["status"] = "abstained"
                return {
                    "clips": [],
                    "warnings": [
                        "The local planner found no relevant candidates and abstained; no footage was selected."
                    ],
                    "strategy": strategy,
                    "planner": planner_info,
                }
        except (ImportError, OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            log.exception("Local evidence planner failed validation; using the deterministic planner")
            reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            warnings.append(
                f"Local planner failed or returned a rejected plan ({reason}); using the deterministic baseline."
            )
            planner_info = {"status": "fallback", "model": planner_name, "reason": reason}
    candidates = []
    for hit in hits[:30]:
        try:
            start, end = float(hit["start"]), float(hit["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration:
            continue
        evidence_id = str(hit.get("id", ""))
        if not evidence_id or len(evidence_id) > 128 or not hit.get("evidence"):
            continue
        evidence = hit["evidence"]
        if any(e.get("modality") == "speech" for e in evidence):
            subtitle = " ".join(str(e["text"]) for e in evidence if e.get("modality") == "speech")[:2000]
        else:
            # Screen observations and generated labels must not become fake spoken subtitles.
            subtitle = ""
        candidates.append(
            {
                "start": max(0, start - 1.5),
                "end": min(duration, end + 1.5),
                "title": str(hit.get("text", "Selected evidence"))[:160],
                "subtitle": subtitle,
                "evidence_ids": [evidence_id],
                "_core_start": start,
                "_core_end": end,
            }
        )
        if len(candidates) == 8:
            break
    if strategy == "deterministic_evidence_timeline_v1":
        candidates.sort(key=lambda h: (h["start"], h["end"]))
    merged: list[dict[str, Any]] = []
    core_budget_used = 0.0
    omitted = False
    for candidate in candidates:
        if merged and merged[-1]["start"] <= candidate["start"] <= merged[-1]["end"]:
            previous = merged[-1]
            combined_start = min(previous["_core_start"], candidate["_core_start"])
            combined_end = max(previous["_core_end"], candidate["_core_end"])
            extra_core = combined_end - combined_start - (previous["_core_end"] - previous["_core_start"])
            if core_budget_used + extra_core > target_seconds:
                omitted = True
                continue
            core_budget_used += extra_core
            previous["end"] = max(previous["end"], candidate["end"])
            previous["_core_start"] = combined_start
            previous["_core_end"] = combined_end
            previous["evidence_ids"] = list(
                dict.fromkeys(previous["evidence_ids"] + candidate["evidence_ids"])
            )
            if candidate["subtitle"] and candidate["subtitle"] not in previous["subtitle"]:
                previous["subtitle"] = (previous["subtitle"] + " " + candidate["subtitle"]).strip()[:2000]
        else:
            core_length = candidate["_core_end"] - candidate["_core_start"]
            if core_budget_used + core_length > target_seconds:
                omitted = True
                continue
            core_budget_used += core_length
            merged.append(candidate)
    if omitted:
        warnings.append(
            "Some evidence did not fit the target duration and was omitted; the edit may cover only part of the request."
        )
    clips = []
    remaining = min(target_seconds, 600)
    reserved_core = core_budget_used
    for candidate in merged:
        if remaining < 0.1:
            break
        core_length = candidate["_core_end"] - candidate["_core_start"]
        reserved_core -= core_length
        # Context cannot consume the budget reserved for later accepted evidence.
        available = max(core_length, remaining - max(0.0, reserved_core))
        length = min(candidate["end"] - candidate["start"], available)
        if length < candidate["end"] - candidate["start"]:
            extra = max(0.0, available - core_length)
            candidate["start"] = max(candidate["start"], candidate["_core_start"] - extra / 2)
            candidate["end"] = min(candidate["end"], candidate["start"] + available)
            length = candidate["end"] - candidate["start"]
            warnings.append(
                "Context around an event was shortened to the target duration; the cited interval remains intact."
            )
        candidate.pop("_core_start", None)
        candidate.pop("_core_end", None)
        candidate["id"] = _id(
            "clip", candidate["start"], candidate["end"], "|".join(candidate["evidence_ids"])
        )
        clips.append(candidate)
        remaining -= length
    if not clips:
        warnings.append("No valid source-backed intervals remained after validation.")
    else:
        clips = validate_clips(clips, duration)
    total = sum(c["end"] - c["start"] for c in clips)
    if total < target_seconds - 1:
        warnings.append(
            f"The available evidence produced {total:.1f}s, shorter than the {target_seconds:.1f}s target; no filler was added."
        )
    if any(hit.get("uncertain") for hit in hits):
        warnings.append(
            "Some candidates are uncertain. Review the source frames and speech before exporting."
        )
    return {"clips": clips, "warnings": warnings, "strategy": strategy, "planner": planner_info}
