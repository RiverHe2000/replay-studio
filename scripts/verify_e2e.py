"""Exercise a running API with real upload, worker, search and MP4 rendering.

Creates a clearly labelled verification project. Does not delete existing data.
Credentials come from REPLAY_VERIFY_EMAIL/PASSWORD, not the process command line.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx


def probe(path: Path) -> dict:
    process = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(process.stdout)


def run(args) -> dict:
    args.out.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "started_at": datetime.now(UTC).isoformat(),
        "fixture": str(args.video.resolve()),
        "evidence_type": "scripted fixture technical integration; not a user study",
        "checks": {},
    }
    stop = threading.Event()
    runner: threading.Thread | None = None
    worker_errors: list[str] = []
    local_store = None
    if args.run_worker:
        from replay_studio.config import Config
        from replay_studio.store import Store
        from replay_studio.worker import process_one

        local_store = Store(Config.from_env())

        verification_worker_id = "e2e-" + uuid.uuid4().hex[:8]

        def local_worker():
            try:
                while not stop.is_set():
                    if not process_one(local_store, worker_id=verification_worker_id):
                        stop.wait(0.1)
            except Exception as exc:
                worker_errors.append(f"{type(exc).__name__}: {exc}")

        runner = threading.Thread(target=local_worker, name="verification-worker", daemon=True)
        runner.start()

    try:
        with httpx.Client(
            base_url=args.base_url.rstrip("/") + "/api", timeout=60, follow_redirects=True
        ) as client:

            def request(method: str, path: str, expected: int | None = 200, **kwargs):
                response = client.request(method, path, **kwargs)
                accepted = (
                    200 <= response.status_code < 300
                    if expected is None
                    else response.status_code == expected
                )
                if not accepted:
                    raise RuntimeError(
                        f"{method} {path}: expected {expected}, got {response.status_code}: {response.text[:600]}"
                    )
                return response

            def post(path: str, data: dict, expected=None):
                return request("POST", path, expected=expected, json=data).json()

            report["capabilities"] = request("GET", "/capabilities").json()
            auth = request("GET", "/auth/status").json()
            email = os.getenv("REPLAY_VERIFY_EMAIL", "verification@example.invalid")
            password = os.getenv("REPLAY_VERIFY_PASSWORD")
            if not password:
                if not auth["needs_setup"]:
                    raise RuntimeError(
                        "Set REPLAY_VERIFY_EMAIL and REPLAY_VERIFY_PASSWORD for an existing account"
                    )
                password = uuid.uuid4().hex + "-Aa1!"
                # Generated setup credentials are deliberately not written to evidence.
            credentials = {"email": email, "password": password}
            if auth["needs_setup"]:
                result = post("/auth/register", {**credentials, "name": "Scripted verification"})
            else:
                result = post("/auth/login", credentials)
            client.headers["X-CSRF-Token"] = result["csrf_token"]
            project = post(
                "/projects",
                {
                    "name": "SCRIPTED FIXTURE / pipeline verification "
                    + datetime.now(UTC).strftime("%H:%M:%S")
                },
            )
            report["project_id"] = project["id"]
            project_path = f"/projects/{project['id']}"
            blob = args.video.read_bytes()
            upload = post(project_path + "/uploads", {"filename": args.video.name, "size": len(blob)})
            chunk_size = upload["chunk_size"]
            for offset in range(0, len(blob), chunk_size):
                chunk = blob[offset : offset + chunk_size]
                path = f"/uploads/{upload['id']}?offset={offset}"
                uploaded = request(
                    "PUT", path, content=chunk, headers={"Content-Type": "application/octet-stream"}
                ).json()
                assert uploaded["offset"] == offset + len(chunk)
                if offset == 0:
                    replay = request(
                        "PUT", path, content=chunk, headers={"Content-Type": "application/octet-stream"}
                    ).json()
                    assert replay["offset"] == uploaded["offset"]
                    changed = bytes([chunk[0] ^ 1]) + chunk[1:]
                    request(
                        "PUT",
                        path,
                        expected=409,
                        content=changed,
                        headers={"Content-Type": "application/octet-stream"},
                    )
            report["checks"]["upload_replay_and_conflict"] = "pass"
            asset = post(f"/uploads/{upload['id']}/complete", {})
            report["asset_id"] = asset["id"]
            report["run_id"] = asset["run_id"]
            analysis_start = time.monotonic()

            def wait_for(predicate):
                deadline = time.monotonic() + args.timeout
                while time.monotonic() < deadline:
                    if worker_errors:
                        raise RuntimeError(worker_errors[-1])
                    snapshot = request("GET", project_path).json()
                    if predicate(snapshot):
                        return snapshot
                    bad = [j for j in snapshot["jobs"] if j["status"] in {"failed", "blocked", "cancelled"}]
                    if bad:
                        raise RuntimeError("pipeline did not complete: " + json.dumps(bad)[:1200])
                    time.sleep(0.5)
                raise TimeoutError("pipeline exceeded verification timeout")

            snapshot = wait_for(
                lambda s: any(a["id"] == asset["id"] and a["status"] == "ready" for a in s["assets"])
            )
            report["analysis_wall_seconds"] = round(time.monotonic() - analysis_start, 3)
            report["jobs"] = snapshot["jobs"]
            transcript = request("GET", f"/assets/{asset['id']}/transcript").json()
            report["transcript"] = transcript
            query = "DATABASE_URL is missing environment file tests passed health"
            report["search"] = {
                mode: post(f"/assets/{asset['id']}/search", {"query": query, "mode": mode, "limit": 8})
                for mode in ("speech", "speech_ocr", "fusion")
            }
            if not report["search"]["speech_ocr"]["hits"]:
                raise AssertionError("real ASR/OCR pipeline returned no evidence for the fixture query")
            report["checks"]["real_evidence_search"] = "pass"
            plan = post(
                f"/assets/{asset['id']}/plan", {"query": query, "mode": "speech_ocr", "target_seconds": 20}
            )
            if not plan["clips"]:
                raise AssertionError("planner did not produce clips supported by evidence")
            report["plan"] = plan
            timeline = snapshot["timeline"]
            edit = {
                "version": timeline["version"],
                "asset_id": asset["id"],
                "run_id": plan["run_id"],
                "clips": plan["clips"],
            }
            saved = request("PUT", project_path + "/timeline", json=edit).json()
            request("PUT", project_path + "/timeline", expected=409, json=edit)
            report["checks"]["timeline_version_conflict"] = "pass"
            export = post(project_path + "/exports", {"version": saved["version"]})
            report["export_id"] = export["id"]
            wait_for(lambda s: any(e["id"] == export["id"] and e["status"] == "ready" for e in s["exports"]))
            for kind, filename in (
                ("video", "output.mp4"),
                ("subtitles", "output.srt"),
                ("provenance", "sources.json"),
            ):
                data = request("GET", f"/exports/{export['id']}/{kind}").content
                (args.out / filename).write_bytes(data)
            metadata = probe(args.out / "output.mp4")
            expected_duration = sum(c["end"] - c["start"] for c in plan["clips"])
            actual_duration = float(metadata["format"]["duration"])
            if abs(actual_duration - expected_duration) > 0.3:
                raise AssertionError(f"render duration mismatch: {actual_duration} vs {expected_duration}")
            report["render"] = {
                "expected_duration": expected_duration,
                "actual_duration": actual_duration,
                "probe": metadata,
            }
            report["checks"]["real_ffmpeg_render"] = "pass"
            if local_store is not None:
                manifest, base = local_store.complete_manifest(asset["run_id"])
                report["stages"] = {
                    stage: {
                        k: v
                        for k, v in manifest[stage].items()
                        if k in {"status", "backend", "model", "warning"}
                    }
                    for stage in ("asr", "ocr", "visual")
                }
                required = [stage.strip() for stage in args.require_modalities.split(",") if stage.strip()]
                unavailable = [name for name in required if manifest[name].get("status") != "complete"]
                if unavailable:
                    raise AssertionError("required modalities did not run: " + ", ".join(unavailable))
                ground_truth = args.video.parent / "ground_truth.json"
                if ground_truth.is_file():
                    from replay_studio.evaluation import evaluate

                    gold = json.loads(ground_truth.read_text(encoding="utf-8"))
                    cases = [
                        {
                            "id": item["id"],
                            "query": item["query"],
                            "intervals": [{"start": item["start"], "end": item["end"]}],
                            "type": "scripted_location",
                            "group": "scripted_debugging",
                            "split": "development",
                            "answerable": True,
                        }
                        for item in gold["queries"]
                    ]
                    cases += [
                        {
                            "id": f"negative-{i}",
                            "query": query,
                            "intervals": [],
                            "type": "hard_negative",
                            "group": "scripted_debugging",
                            "split": "development",
                            "answerable": False,
                        }
                        for i, query in enumerate(gold["hard_negatives"])
                    ]
                    report["retrieval_evaluation"] = evaluate(cases, manifest, base)
            report["status"] = "pass"
    except Exception as exc:
        report["status"] = "fail"
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        stop.set()
        if runner:
            runner.join(timeout=5)
        report["finished_at"] = datetime.now(UTC).isoformat()
        (args.out / "verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--video", type=Path, default=Path("data/fixtures/debugging/scripted_debugging.mp4"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/verification"))
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument(
        "--run-worker",
        action="store_true",
        help="Run a real local worker sharing the API's REPLAY_* configuration",
    )
    parser.add_argument(
        "--require-modalities",
        default="asr,ocr",
        help="With local worker, require these stages to finish with real results",
    )
    summary = run(parser.parse_args())
    print(
        json.dumps(
            {
                "status": summary["status"],
                "checks": summary["checks"],
                "analysis_wall_seconds": summary["analysis_wall_seconds"],
            },
            indent=2,
        )
    )
