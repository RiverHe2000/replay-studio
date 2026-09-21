"""Bounded media processing on a normalized playback timeline.

FFmpeg always receives an argument vector; user text never enters a filter graph.
Source video PTS is the clock. Audio is shifted by that *same* origin, padded or
trimmed as needed, rather than independently resetting its first sample to zero.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

Cancel = Callable[[], bool] | None
MAX_DURATION = 1800.0
MAX_EXPORT_DURATION = 600.0
MAX_CLIPS = 30
_FORMATS = "mov,matroska,webm,avi,mpegts,mpeg,ogg,flv"


class MediaError(RuntimeError):
    """Invalid media or a failed external media operation."""


class OperationCancelled(RuntimeError):
    """A stage lost its lease or was explicitly cancelled."""


def check_cancel(cancel: Cancel) -> None:
    if cancel is not None and cancel():
        raise OperationCancelled("Operation cancelled")


def safe_path(base: Path, relative: str) -> Path:
    """Resolve a committed artifact without allowing traversal or symlink escape."""
    path = Path(relative)
    if not relative or path.is_absolute() or path.drive or ".." in path.parts:
        raise ValueError("Artifact path must be relative and stay within its directory")
    # On POSIX, a Windows path still must not be accepted as a portable artifact.
    if "\\" in relative or ":" in relative:
        raise ValueError("Artifact path must use portable relative names")
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(base.resolve()):
        raise ValueError("Artifact path escapes its directory")
    return resolved


def _run(
    args: Sequence[str],
    *,
    cancel: Cancel = None,
    timeout: float = 7200.0,
    output_limit: int = 4 * 1024 * 1024,
) -> bytes:
    check_cancel(cancel)
    if not shutil.which(args[0]):
        raise MediaError(f"Required executable is unavailable: {args[0]}")
    # Files avoid pipe deadlocks and bound resident memory even on noisy inputs.
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            list(args),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=flags,
        )
        started = time.monotonic()
        try:
            while process.poll() is None:
                check_cancel(cancel)
                if time.monotonic() - started > timeout:
                    raise MediaError(f"{Path(args[0]).name} exceeded its time limit")
                time.sleep(0.1)
            check_cancel(cancel)
            if process.returncode:
                stderr.seek(0, 2)
                stderr.seek(max(0, stderr.tell() - 4000))
                detail = stderr.read().decode("utf-8", errors="replace").strip()
                raise MediaError(f"{Path(args[0]).name} failed: {detail[-2000:]}")
            stdout.seek(0)
            raw = stdout.read(output_limit + 1)
            if len(raw) > output_limit:
                raise MediaError("External tool output exceeded its resource limit")
            return raw
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)


def _number(value: Any, default: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        if default is None:
            raise MediaError("Media has no usable numeric duration") from None
        return default
    if not math.isfinite(result):
        if default is None:
            raise MediaError("Media contains a non-finite numeric value")
        return default
    return result


def probe(source: Path, *, cancel: Cancel = None) -> dict[str, Any]:
    source = Path(source).resolve()
    if not source.is_file():
        raise MediaError("Source media does not exist")
    raw = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            _FORMATS,
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(source),
        ],
        cancel=cancel,
        timeout=45,
    )
    try:
        data = json.loads(raw)
        streams = data.get("streams", [])
        video = next(
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        )
    except (json.JSONDecodeError, StopIteration, TypeError) as exc:
        raise MediaError("Media must contain a decodable video stream") from exc
    fmt = data.get("format", {})
    origin = _number(video.get("start_time"), _number(fmt.get("start_time"), 0.0))
    duration = _number(video.get("duration"), 0.0)
    if duration <= 0:
        # Matroska's format duration can be an absolute end timestamp. Inspect
        # packet PTS when stream duration is absent; subtracting start_time from
        # every container's duration is incorrect (e.g. MPEG-TS semantics differ).
        packets = _run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file,pipe",
                "-format_whitelist",
                _FORMATS,
                "-select_streams",
                str(video["index"]),
                "-read_intervals",
                "%+#216001",
                "-show_entries",
                "packet=pts_time,duration_time",
                "-of",
                "csv=p=0",
                str(source),
            ],
            cancel=cancel,
            timeout=90,
            output_limit=16 * 1024 * 1024,
        )
        points = []
        for line in packets.decode("utf-8", errors="replace").splitlines():
            fields = line.split(",")
            if len(fields) < 2 or fields[0] in ("", "N/A"):
                continue
            pts = _number(fields[0])
            packet_duration = _number(fields[1], 0.0)
            points.append((pts, packet_duration))
        if not points or len(points) > 216000:
            raise MediaError("Video timestamp inspection failed or exceeds 216000 frames")
        origin = min(point[0] for point in points)
        positive_durations = [point[1] for point in points if point[1] > 0]
        fallback = min(positive_durations) if positive_durations else 1 / 30
        duration = max(pts + (span if span > 0 else fallback) for pts, span in points) - origin
    if duration <= 0:
        raise MediaError("Media has no finite positive duration")
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    if width < 2 or height < 2 or width * height > 33_177_600:
        raise MediaError("Video dimensions are missing or exceed the 8K decode limit")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "time_origin": origin,
        "has_audio": audio is not None,
        "video_index": int(video["index"]),
        "audio_index": int(audio["index"]) if audio else None,
        "audio_origin": _number(audio.get("start_time"), origin) if audio else None,
        "format": fmt.get("format_name", "unknown"),
    }


def _ffmpeg_input(source: Path) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        _FORMATS,
        "-i",
        str(source.resolve()),
    ]


def _write_json(path: Path, data: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def prepare(source: Path, out: Path, *, cancel: Cancel = None) -> dict[str, Any]:
    source, out = Path(source).resolve(), Path(out).resolve()
    info = probe(source, cancel=cancel)
    if info["duration"] > MAX_DURATION + 0.001:
        raise MediaError("Video exceeds the 30 minute limit")
    out.mkdir(parents=True, exist_ok=True)
    if source.is_relative_to(out):
        raise MediaError("Source media must be outside the artifact directory")
    frames_dir = out / "frames"
    frames_dir.mkdir(exist_ok=True)
    origin, duration = info["time_origin"], info["duration"]
    proxy = out / "proxy.mp4"
    # -copyts preserves the relationship between independently starting streams.
    args = _ffmpeg_input(source)
    args[args.index("-i") : args.index("-i")] = ["-copyts"]
    vf = (
        f"setpts=PTS-({origin:.9f})/TB,"
        "scale=w='min(1920,iw)':h='min(1080,ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps=30"
    )
    args += [
        "-map",
        f"0:{info['video_index']}",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-threads",
        "2",
    ]
    if info["has_audio"]:
        af = (
            f"asetpts=PTS-({origin:.9f})/TB,aresample=48000:async=1:first_pts=0,"
            f"apad,atrim=duration={duration:.9f}"
        )
        args += [
            "-map",
            f"0:{info['audio_index']}",
            "-af",
            af,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ac",
            "2",
            "-ar",
            "48000",
        ]
    else:
        args += ["-an"]
    args += ["-t", f"{duration:.9f}", "-map_metadata", "-1", "-movflags", "+faststart", str(proxy)]
    _run(args, cancel=cancel, timeout=max(120, duration * 8))
    normalized = probe(proxy, cancel=cancel)
    duration = normalized["duration"]
    if duration > MAX_DURATION + 0.1:
        raise MediaError("Normalized video exceeds the duration limit")
    audio_path: str | None = None
    if normalized["has_audio"]:
        _run(
            _ffmpeg_input(proxy)
            + ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out / "audio.wav")],
            cancel=cancel,
            timeout=max(90, duration),
        )
        audio_path = "audio.wav"
    interval = max(2.0, duration / 899.0)
    # round=up chooses the last input frame at/before each output timestamp.
    # The default round=near would silently assign a frame near t=1s to t=0
    # at a 2s sampling interval, breaking OCR/visual evidence timestamps.
    _run(
        _ffmpeg_input(proxy)
        + [
            "-an",
            "-vf",
            f"fps=1/{interval:.9f}:round=up:start_time=0",
            "-frames:v",
            "900",
            "-q:v",
            "3",
            str(frames_dir / "frame_%06d.jpg"),
        ],
        cancel=cancel,
        timeout=max(120, duration * 2),
    )
    frames = [
        {"time": round(i * interval, 6), "path": f"frames/{p.name}"}
        for i, p in enumerate(sorted(frames_dir.glob("frame_*.jpg")))
        if i * interval < duration
    ]
    if not frames:
        _run(
            _ffmpeg_input(proxy) + ["-frames:v", "1", "-q:v", "3", str(frames_dir / "frame_000001.jpg")],
            cancel=cancel,
            timeout=60,
        )
        frames = [{"time": 0.0, "path": "frames/frame_000001.jpg"}]
    result = {
        "duration": duration,
        "width": normalized["width"],
        "height": normalized["height"],
        "has_audio": normalized["has_audio"],
        "proxy": "proxy.mp4",
        "audio": audio_path,
        "frames": frames,
        "time_origin": origin,
        "warnings": [f"Frames sampled every {interval:.2f}s; briefer visual events may be missed."],
        "timeline": {
            "unit": "seconds",
            "basis": "normalized_video",
            "source_pts_origin": origin,
            "proxy_fps": 30,
            "audio_source_pts_origin": info["audio_origin"],
        },
    }
    _write_json(out / "prepare.json", result)
    return result


def validate_clips(clips: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    if not isinstance(clips, list) or not 1 <= len(clips) <= MAX_CLIPS:
        raise ValueError(f"Provide between 1 and {MAX_CLIPS} clips")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Source duration must be finite and positive")
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0.0
    for item in clips:
        if not isinstance(item, dict):
            raise ValueError("Every clip must be an object")
        try:
            start, end = float(item["start"]), float(item["end"])
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError("Clip boundaries must be numeric") from exc
        if isinstance(item["start"], bool) or isinstance(item["end"], bool):
            raise ValueError("Clip boundaries cannot be booleans")
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration:
            raise ValueError("Clip boundaries must be finite, increasing and within the video")
        if end - start < 1 / 30:
            raise ValueError("Every clip must contain at least one video frame")
        ident = item.get("id")
        if not isinstance(ident, str) or not ident or len(ident) > 128 or ident in seen:
            raise ValueError("Clip IDs must be unique nonempty strings, at most 128 characters")
        seen.add(ident)
        title, subtitle = item.get("title", ""), item.get("subtitle", "")
        if not isinstance(title, str) or not isinstance(subtitle, str):
            raise ValueError("Clip title and subtitle must be text")
        if len(title) > 300 or len(subtitle) > 5000:
            raise ValueError("Clip text exceeds its length limit")
        evidence = item.get("evidence_ids", [])
        if (
            not isinstance(evidence, list)
            or len(evidence) > 100
            or any(not isinstance(e, str) or len(e) > 128 for e in evidence)
        ):
            raise ValueError("Invalid evidence IDs")
        total += end - start
        clean.append(
            {
                "id": ident,
                "start": start,
                "end": end,
                "title": title,
                "subtitle": subtitle,
                "evidence_ids": evidence,
            }
        )
    if total > MAX_EXPORT_DURATION + 1e-6:
        raise ValueError("Export duration exceeds 10 minutes")
    return clean


def _srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    sec, millis = divmod(millis, 1000)
    return f"{hours:02}:{minutes:02}:{sec:02},{millis:03}"


def _caption(text: str) -> str:
    # Plain subtitle text cannot create extra cues or markup interpreted by a player.
    return " ".join(text.replace("\x00", "").replace("-->", "→").split()).replace("<", "‹").replace(">", "›")


def render(source: Path, clips: list[dict[str, Any]], out: Path, *, cancel: Cancel = None) -> dict[str, Any]:
    source, out = Path(source).resolve(), Path(out).resolve()
    info = probe(source, cancel=cancel)
    clean = validate_clips(clips, info["duration"])
    out.mkdir(parents=True, exist_ok=True)
    if source.is_relative_to(out):
        raise MediaError("Source media must be outside the export directory")
    has_audio = info["has_audio"]
    provenance: list[dict[str, Any]] = []
    subtitles: list[str] = []
    cursor = 0.0
    # Sequential segment encodes cap file descriptors and filter memory on long plans.
    with tempfile.TemporaryDirectory(prefix="render-", dir=out) as temporary:
        work = Path(temporary)
        segment_paths: list[Path] = []
        for index, clip in enumerate(clean):
            check_cancel(cancel)
            part = work / f"part_{index:03d}.mp4"
            length = clip["end"] - clip["start"]
            args = _ffmpeg_input(source)
            # FFmpeg's default accurate input seek decodes/discards from the prior
            # keyframe; reencoding gives precise cuts without decoding 30 minutes
            # from the beginning for every selected clip.
            at_input = args.index("-i")
            args[at_input:at_input] = ["-ss", f"{clip['start']:.9f}"]
            args += [
                "-t",
                f"{length:.9f}",
                "-map",
                "0:v:0",
                "-vf",
                "setpts=PTS-STARTPTS,fps=30,setsar=1",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-threads",
                "2",
            ]
            if has_audio:
                args += [
                    "-map",
                    "0:a:0",
                    "-af",
                    "asetpts=PTS-STARTPTS",
                    "-c:a",
                    "aac",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-b:a",
                    "128k",
                ]
            else:
                args += ["-an"]
            args += ["-map_metadata", "-1", str(part)]
            _run(args, cancel=cancel, timeout=max(90, length * 10))
            actual = probe(part, cancel=cancel)["duration"]
            segment_paths.append(part)
            provenance.append(
                {
                    **clip,
                    "source_start": clip["start"],
                    "source_end": clip["end"],
                    "output_start": round(cursor, 6),
                    "output_end": round(cursor + actual, 6),
                }
            )
            if clip["subtitle"].strip():
                subtitles.append(
                    f"{len(subtitles) + 1}\n{_srt_time(cursor)} --> {_srt_time(cursor + actual)}\n{_caption(clip['subtitle'])}\n"
                )
            cursor += actual
        # The concat file contains only our generated relative names, no user paths.
        concat = work / "segments.txt"
        concat.write_text("".join(f"file '{p.name}'\n" for p in segment_paths), encoding="ascii")
        _run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-nostdin",
                "-y",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(concat),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(out / "output.mp4"),
            ],
            cancel=cancel,
            timeout=max(90, cursor * 2),
        )
    actual_duration = probe(out / "output.mp4", cancel=cancel)["duration"]
    (out / "output.srt").write_text("\n".join(subtitles), encoding="utf-8")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            check_cancel(cancel)
            digest.update(block)
    _write_json(
        out / "sources.json",
        {
            "schema_version": 1,
            "timeline": "normalized_video_seconds",
            "source_sha256": digest.hexdigest(),
            "clips": provenance,
            "requested_duration": sum(c["end"] - c["start"] for c in clean),
            "encoded_duration": actual_duration,
            "boundary_tolerance_seconds": 1 / 30,
            "subtitle_origin": "user-reviewed clip subtitles; not necessarily verbatim transcription",
        },
    )
    result = {
        "video": "output.mp4",
        "subtitles": "output.srt",
        "provenance": "sources.json",
        "duration": actual_duration,
    }
    _write_json(out / "render.json", result)
    return result
