"""Generate a labelled scripted screencast, never represented as user study data."""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCENES = [
    {
        "id": "failure",
        "title": "01  Reproduce the startup error",
        "accent": "#fb7185",
        "lines": [
            "$ python -m replay_studio.cli serve",
            "Loading application configuration...",
            "",
            "ERROR: DATABASE_URL is missing",
            "Startup aborted. No database connection.",
            "",
            "Next: inspect the environment file.",
        ],
        "speech": "The application fails to start. The error says database URL is missing. We need to inspect the environment file.",
        "query": "Where does the application fail because DATABASE_URL is missing?",
    },
    {
        "id": "fix",
        "title": "02  Correct the environment configuration",
        "accent": "#fbbf24",
        "lines": [
            ".env",
            "",
            "DATABASE_URL=postgresql://localhost/replay",
            "PORT=8080",
            "",
            "Saved .env",
            "Restart the application to load the change.",
        ],
        "speech": "Open the environment file. Add the database URL for local Postgres and save the file. Now restart the application.",
        "query": "Find the moment when the database connection setting is added to the environment file.",
    },
    {
        "id": "success",
        "title": "03  Verify the fix",
        "accent": "#4ade80",
        "lines": [
            "$ python -m replay_studio.cli serve",
            "Database connected",
            "Application startup complete",
            "",
            "$ pytest",
            "12 passed in 0.84s",
            "GET /health -> 200 OK",
        ],
        "speech": "The database is connected and the application starts. The verification reports twelve tests passed and the health endpoint returns two hundred OK.",
        "query": "Show the successful startup, passing tests, and healthy endpoint after the fix.",
    },
]


def command(args: list[str]) -> None:
    subprocess.run(args, check=True, timeout=180, capture_output=True)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        f"C:/Windows/Fonts/{'consolab' if bold else 'consola'}.ttf",
        f"/usr/share/fonts/truetype/dejavu/DejaVuSansMono{'-Bold' if bold else ''}.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf",
    ]
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def speech(text: str, target: Path) -> bool:
    if os.name == "nt" and shutil.which("powershell"):
        quoted_text = text.replace("'", "''")
        quoted_path = str(target.resolve()).replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$speaker.Rate = 0; "
            f"$speaker.SetOutputToWaveFile('{quoted_path}'); "
            f"$speaker.Speak('{quoted_text}'); $speaker.Dispose()"
        )
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        command(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])
        return target.is_file()
    executable = shutil.which("espeak-ng") or shutil.which("espeak")
    if executable:
        command([executable, "-s", "175", "-w", str(target), text])
        return True
    return False


def generate(out: Path, audio: bool, seconds: float) -> dict:
    if seconds < 8 or seconds > 30:
        raise ValueError("scene duration must be between 8 and 30 seconds")
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required")
    out.mkdir(parents=True, exist_ok=True)
    audio_available = audio
    for i, scene in enumerate(SCENES):
        canvas = Image.new("RGB", (1280, 720), "#0b1220")
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, 1280, 64), fill="#142238")
        draw.text(
            (35, 19), "REPLAY STUDIO  /  SCRIPTED TECHNICAL FIXTURE", font=font(24, True), fill="#93c5fd"
        )
        draw.text((40, 93), scene["title"], font=font(29, True), fill=scene["accent"])
        draw.rounded_rectangle((35, 155, 1245, 620), radius=12, fill="#111b2b", outline="#34445c", width=2)
        for n, line in enumerate(scene["lines"]):
            fill = (
                scene["accent"]
                if any(term in line for term in ("ERROR", "DATABASE_URL", "passed", "200 OK"))
                else "#e2e8f0"
            )
            draw.text((65, 180 + 53 * n), line, font=font(26), fill=fill)
        draw.text(
            (40, 665),
            f"Synthetic demonstration | scene {i + 1}/3 | No real production incident",
            font=font(20),
            fill="#94a3b8",
        )
        image_path = out / f"scene_{i}.png"
        canvas.save(image_path)
        wav = out / f"scene_{i}.wav"
        if audio and not speech(scene["speech"], wav):
            audio_available = False
        args = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-loop",
            "1",
            "-framerate",
            "15",
            "-i",
            str(image_path),
        ]
        if audio and wav.is_file():
            args += ["-i", str(wav), "-af", "apad", "-c:a", "aac", "-ar", "48000"]
        else:
            args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-c:a", "aac"]
        args += [
            "-t",
            str(seconds),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out / f"part_{i}.mp4"),
        ]
        command(args)
    listing = out / "concat.txt"
    listing.write_text("".join(f"file 'part_{i}.mp4'\n" for i in range(3)), encoding="utf-8")
    final = out / "scripted_debugging.mp4"
    command(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            str(listing),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            "-metadata",
            "comment=SCRIPTED FIXTURE: generated images and optional synthetic narration, not a real incident or user study",
            str(final),
        ]
    )
    gold = {
        "fixture_type": "scripted",
        "not_real_user_data": True,
        "video": final.name,
        "duration": seconds * 3,
        "audio": "synthetic narration" if audio_available else "silence",
        "queries": [
            {
                "id": scene["id"],
                "query": scene["query"],
                "start": i * seconds,
                "end": (i + 1) * seconds,
                "modalities": ["screen", "speech"] if audio_available else ["screen"],
            }
            for i, scene in enumerate(SCENES)
        ],
        "hard_negatives": [
            "Where is the Kubernetes cluster upgraded?",
            "Show the payment refund confirmation.",
        ],
    }
    (out / "ground_truth.json").write_text(json.dumps(gold, indent=2), encoding="utf-8")
    return gold


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/fixtures/debugging"))
    parser.add_argument(
        "--audio", action="store_true", help="Use installed Windows TTS or espeak; no fake ASR labels"
    )
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()
    try:
        print(json.dumps(generate(args.out, args.audio, args.seconds), indent=2))
    except subprocess.CalledProcessError as exc:
        print(exc.stderr.decode(errors="replace"), file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
