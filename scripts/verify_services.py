"""Actual PostgreSQL claim fencing and S3 media round-trip against isolated services."""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from replay_studio.config import Config
from replay_studio.storage import Storage
from replay_studio.store import Store
from replay_studio.worker import process_one


def run(config: Config, video: Path, output: Path) -> dict:
    if not config.database_url.startswith("postgresql") or config.storage_backend != "s3":
        raise ValueError("This verification requires actual PostgreSQL and S3 configuration")
    store = Store(config)
    report = {"started_at": datetime.now(UTC).isoformat(), "profile": "postgresql+s3", "checks": {}}
    account = store.register(
        "services-" + uuid.uuid4().hex[:8] + "@example.invalid",
        uuid.uuid4().hex,
        "Isolated service verification",
    )
    user_id = account["user"]["id"]
    project = store.create_project(user_id, "SCRIPTED FIXTURE / PostgreSQL and S3 invariants")
    data = video.read_bytes()
    upload = store.init_upload(project["id"], user_id, video.name, len(data))
    for offset in range(0, len(data), config.chunk_size):
        store.append_upload(upload["id"], user_id, offset, data[offset : offset + config.chunk_size])
    asset = store.complete_upload(upload["id"], user_id)
    report.update(asset_id=asset["id"], project_id=project["id"], run_id=asset["run_id"])
    independent = Storage(replace(config, data_dir=config.data_dir / "independent-readback"))
    actual = independent.local_path(f"assets/{asset['id']}/source").read_bytes()
    assert hashlib.sha256(actual).digest() == hashlib.sha256(data).digest()
    report["checks"]["s3_download_through_independent_cache"] = "pass"

    def contend(queue: str):
        barrier = threading.Barrier(8)

        def claim(number):
            barrier.wait(timeout=10)
            return store.claim(queue, f"contention-{number}")

        with ThreadPoolExecutor(max_workers=8) as executor:
            return [job for job in executor.map(claim, range(8)) if job is not None]

    claims = contend("cpu")
    assert len(claims) == 1 and claims[0]["kind"] == "prepare"
    first = claims[0]
    report["checks"]["eight_concurrent_prepare_claims_one_owner"] = "pass"
    while time.time() <= first["lease_until"] + 0.1:
        time.sleep(0.05)
    replacement = store.claim("cpu", "replacement")
    assert (
        replacement
        and replacement["id"] == first["id"]
        and replacement["lease_token"] != first["lease_token"]
    )
    assert not store.heartbeat(first["id"], first["lease_token"])
    assert not store.finish(first["id"], first["lease_token"], {"duration": 30})
    assert store.fail(replacement["id"], replacement["lease_token"], "Deliberate lease verification handoff")
    assert process_one(store, queue="cpu", worker_id="real-postgres-worker")
    prepared = next(j for j in store.jobs(project["id"]) if j["kind"] == "prepare")
    assert prepared["status"] == "complete" and prepared["attempts"] == 3
    report["checks"]["postgres_expired_token_fenced_then_real_worker_prepare"] = "pass"
    gpu_claims = contend("gpu")
    assert len(gpu_claims) == 1
    held = gpu_claims[0]
    assert store.heartbeat(held["id"], held["lease_token"])
    assert store.claim("gpu", "cannot-overbook") is None
    assert store.fail(held["id"], held["lease_token"], "Deliberate GPU lease verification handoff")
    report["checks"]["global_gpu_lease_excludes_concurrent_claims"] = "pass"
    store.delete_asset(asset["id"], user_id)
    report.update(status="pass", finished_at=datetime.now(UTC).isoformat(), deleted_probe_asset=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, default=Path("data/fixtures/debugging/scripted_debugging.mp4"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/services/verification.json"))
    options = parser.parse_args()
    print(json.dumps(run(Config.from_env(), options.video, options.out), indent=2))
