from __future__ import annotations

import json
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from replay_studio.media import (
    MediaError,
    OperationCancelled,
    prepare,
    probe,
    render,
    safe_path,
    validate_clips,
)


def ffmpeg(*args: str) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg is required for real media verification")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args],
        check=True,
        timeout=60,
        capture_output=True,
    )


def test_normalization_preserves_delayed_audio_and_nonzero_origin(tmp_path: Path) -> None:
    source = tmp_path / "offset.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x240:r=15:d=3",
        "-itsoffset",
        "0.8",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1.8",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-output_ts_offset",
        "5",
        str(source),
    )
    out = tmp_path / "prepared"
    result = prepare(source, out)
    assert result["time_origin"] == pytest.approx(5, abs=0.04)
    assert result["duration"] == pytest.approx(3, abs=0.04)
    assert probe(out / result["proxy"])["time_origin"] == pytest.approx(0, abs=0.04)
    with wave.open(str(out / result["audio"]), "rb") as stream:
        rate = stream.getframerate()
        samples = np.frombuffer(stream.readframes(stream.getnframes()), dtype=np.int16).astype(float)
    quiet = np.sqrt(np.mean(samples[: int(0.5 * rate)] ** 2))
    tone = np.sqrt(np.mean(samples[int(1.0 * rate) : int(1.5 * rate)] ** 2))
    assert quiet < 20
    assert tone > 500
    assert len(samples) / rate == pytest.approx(3, abs=0.05)
    assert all(safe_path(out, frame["path"]).is_file() for frame in result["frames"])


def test_silent_video_export_uses_actual_clip_duration_and_safe_captions(tmp_path: Path) -> None:
    source = tmp_path / "silent.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x240:rate=30:duration=5",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(source),
    )
    prepared = tmp_path / "prepared"
    result = prepare(source, prepared)
    assert not result["has_audio"] and result["audio"] is None
    assert result["frames"][0]["time"] == 0
    exported = tmp_path / "export"
    clips = [
        {
            "id": "a",
            "start": 0.7,
            "end": 1.8,
            "title": "one",
            "subtitle": "untrusted <script>\n\n2\n00:00:00,000 --> anything",
        },
        {"id": "b", "start": 3.1, "end": 4.5, "title": "two", "subtitle": "second"},
    ]
    final = render(prepared / result["proxy"], clips, exported)
    assert final["duration"] == pytest.approx(2.5, abs=0.07)
    assert not probe(exported / final["video"])["has_audio"]
    captions = (exported / final["subtitles"]).read_text(encoding="utf-8")
    assert captions.count(" --> ") == 2
    assert "<script>" not in captions
    provenance = json.loads((exported / final["provenance"]).read_text())
    assert len(provenance["clips"]) == 2
    assert provenance["clips"][1]["source_start"] == 3.1
    assert provenance["clips"][1]["output_start"] == pytest.approx(1.1, abs=0.04)
    assert len(provenance["source_sha256"]) == 64


@pytest.mark.parametrize(
    "start,end", [(float("nan"), 2), (0, float("inf")), (-1, 2), (2, 1), (0, 11), (True, 3)]
)
def test_export_boundaries_fail_closed(start: float, end: float) -> None:
    with pytest.raises(ValueError):
        validate_clips([{"id": "x", "start": start, "end": end}], 10)


def test_export_rejects_duplicate_ids_and_oversized_plan() -> None:
    with pytest.raises(ValueError, match="unique"):
        validate_clips([{"id": "x", "start": 0, "end": 2}] * 2, 10)
    with pytest.raises(ValueError, match="10 minutes"):
        validate_clips([{"id": "x", "start": 0, "end": 601}], 700)


@pytest.mark.parametrize(
    "path", ["../outside.jpg", "/root/image.jpg", "C:/secret.jpg", "frames/../../other", "frames\\x.jpg"]
)
def test_artifact_path_traversal_is_rejected(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError):
        safe_path(tmp_path, path)


def test_cancelled_prepare_does_not_launch_encoder(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not a video")
    with pytest.raises(OperationCancelled):
        prepare(source, tmp_path / "out", cancel=lambda: True)
    assert not (tmp_path / "out").exists()


def test_invalid_media_rejected(tmp_path: Path) -> None:
    source = tmp_path / "invalid.mp4"
    source.write_text("not a movie")
    with pytest.raises(MediaError):
        prepare(source, tmp_path / "out")


def test_vfr_matroska_nonzero_origin_uses_packet_span(tmp_path: Path) -> None:
    source = tmp_path / "variable.mkv"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x120:rate=30:duration=3",
        "-vf",
        "select='if(lt(t,1),1,not(mod(n,3)))'",
        "-fps_mode",
        "vfr",
        "-c:v",
        "libx264",
        "-output_ts_offset",
        "5",
        str(source),
    )
    metadata = probe(source)
    assert metadata["time_origin"] == pytest.approx(5, abs=0.04)
    assert 2.8 < metadata["duration"] <= 3.05
    result = prepare(source, tmp_path / "prepared")
    assert result["duration"] == pytest.approx(metadata["duration"], abs=0.05)
    assert result["timeline"]["basis"] == "normalized_video"


def test_render_selects_requested_visual_content_and_keeps_audio_aligned(tmp_path: Path) -> None:
    source = tmp_path / "colors.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=red:s=160x120:r=30:d=1",
        "-f",
        "lavfi",
        "-i",
        "color=blue:s=160x120:r=30:d=1",
        "-f",
        "lavfi",
        "-i",
        "color=green:s=160x120:r=30:d=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=800:duration=3",
        "-filter_complex",
        "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
        "-map",
        "[v]",
        "-map",
        "3:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(source),
    )
    output = tmp_path / "export"
    result = render(
        source,
        [
            {"id": "blue", "start": 1.2, "end": 1.8, "subtitle": "Blue"},
            {"id": "green", "start": 2.2, "end": 2.8, "subtitle": "Green"},
        ],
        output,
    )
    assert result["duration"] == pytest.approx(1.2, abs=0.04)
    for name, stamp, channel in (("blue", 0.1, 2), ("green", 0.7, 1)):
        image = tmp_path / f"{name}.png"
        ffmpeg("-ss", str(stamp), "-i", str(output / result["video"]), "-frames:v", "1", str(image))
        with Image.open(image) as frame:
            pixel = frame.convert("RGB").getpixel((80, 60))
        assert pixel[channel] > max(pixel[i] for i in range(3) if i != channel) + 50
    raw = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output / result["video"])],
        check=True,
        capture_output=True,
        timeout=30,
    )
    streams = json.loads(raw.stdout)["streams"]
    durations = {stream["codec_type"]: float(stream["duration"]) for stream in streams}
    assert abs(durations["video"] - durations["audio"]) <= 0.04


def test_many_audio_cuts_do_not_accumulate_encoder_padding(tmp_path: Path) -> None:
    source = tmp_path / "audio.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=blue:s=64x64:r=30:d=1",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(source),
    )
    result = render(source, [{"id": str(i), "start": 0.1, "end": 0.3} for i in range(12)], tmp_path / "out")
    assert result["duration"] == pytest.approx(2.4, abs=0.04)
    provenance = json.loads((tmp_path / "out" / "sources.json").read_text())
    assert provenance["clips"][-1]["output_end"] == pytest.approx(result["duration"], abs=0.04)


def test_sampled_frame_content_matches_its_reported_timestamp(tmp_path: Path) -> None:
    source = tmp_path / "brief-opening.mp4"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "color=red:s=64x64:r=30:d=0.5",
        "-f",
        "lavfi",
        "-i",
        "color=blue:s=64x64:r=30:d=2.5",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map",
        "[v]",
        "-c:v",
        "libx264",
        str(source),
    )
    out = tmp_path / "prepared"
    prepared = prepare(source, out)
    first = prepared["frames"][0]
    assert first["time"] == 0
    with Image.open(out / first["path"]) as image:
        red, green, blue = image.convert("RGB").getpixel((32, 32))
    assert red > blue + 100
