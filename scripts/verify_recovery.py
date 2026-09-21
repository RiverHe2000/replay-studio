"""Kill an actual worker process, reclaim its expired lease, verify real FFmpeg recovery.

Uses an isolated new local data directory. This is a crash-injection technical
experiment, not evidence of high availability across hosts.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from replay_studio import db
from replay_studio.config import Config
from replay_studio.store import Store


def terminate_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=True, timeout=15
        )
    else:
        os.killpg(process.pid, signal.SIGKILL)
    process.wait(timeout=15)


def verify(video: Path, out: Path) -> dict:
    directory = (
        out / ("run-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6])
    ).resolve()
    config = Config(data_dir=directory / "data", lease_seconds=3)
    store = Store(config)
    account = store.register("recovery@example.invalid", uuid.uuid4().hex, "Crash injection fixture")
    user_id = account["user"]["id"]
    project = store.create_project(user_id, "SCRIPTED FIXTURE / actual process crash")
    content = video.read_bytes()
    upload = store.init_upload(project["id"], user_id, video.name, len(content))
    for offset in range(0, len(content), config.chunk_size):
        store.append_upload(upload["id"], user_id, offset, content[offset : offset + config.chunk_size])
    asset = store.complete_upload(upload["id"], user_id)
    env = os.environ.copy()
    env.update(
        REPLAY_DATA_DIR=str(config.data_dir),
        REPLAY_DATABASE_URL=config.database_url,
        REPLAY_STORAGE="local",
        REPLAY_LEASE_SECONDS="3",
    )
    args = [sys.executable, "-m", "replay_studio.cli", "worker", "--queue", "cpu", "--once"]
    processes: list[subprocess.Popen] = []

    def row() -> dict:
        with store.transaction(False) as conn:
            result = (
                conn.execute(
                    select(db.jobs).where(db.jobs.c.asset_id == asset["id"], db.jobs.c.kind == "prepare")
                )
                .mappings()
                .one()
            )
            return dict(result)

    def wait_for(predicate, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = row()
            if predicate(current):
                return current
            time.sleep(0.01)
        raise TimeoutError("worker did not reach the required state")

    report = {
        "experiment": "actual process crash and fenced recovery",
        "source": str(video.resolve()),
        "directory": str(directory),
        "started_at": datetime.now(UTC).isoformat(),
    }
    try:
        with (directory / "first-worker.log").open("w", encoding="utf-8") as log:
            first = subprocess.Popen(
                args, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=os.name != "nt"
            )
            processes.append(first)
            claimed = wait_for(lambda item: item["status"] == "running")
            terminate_tree(first)
        report["terminated_pid"] = first.pid
        report["first_attempt"] = claimed["attempts"]
        if row()["status"] != "running":
            raise AssertionError("first attempt completed before crash injection")
        while time.time() <= claimed["lease_until"] + 0.1:
            time.sleep(0.05)
        with (directory / "replacement-worker.log").open("w", encoding="utf-8") as log:
            second = subprocess.Popen(
                args, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=os.name != "nt"
            )
            processes.append(second)
            replacement = wait_for(lambda item: item["attempts"] == 2 and item["status"] == "running")
            stale_accepted = store.finish(claimed["id"], claimed["lease_token"], {"duration": 30})
            if stale_accepted:
                raise AssertionError("stale owner incorrectly committed over replacement")
            second.wait(timeout=180)
        completed = row()
        if second.returncode != 0 or completed["status"] != "complete":
            raise AssertionError("replacement worker did not complete real prepare")
        if (
            completed["lease_token"] != replacement["lease_token"]
            or completed["artifact_prefix"] == claimed["artifact_prefix"]
        ):
            raise AssertionError("replacement did not use a fresh fenced attempt")
        proxy = store.storage.local_path(
            completed["result"]["artifact_prefix"] + "/" + completed["result"]["proxy"]
        )
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(proxy)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        report.update(
            status="pass",
            replacement_pid=second.pid,
            completed_attempt=completed["attempts"],
            stale_completion_rejected=True,
            immutable_prefix_changed=True,
            proxy=json.loads(probe.stdout),
        )
    except Exception as exc:
        report.update(status="fail", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        for process in processes:
            terminate_tree(process)
        report["finished_at"] = datetime.now(UTC).isoformat()
        (directory / "recovery.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, default=Path("data/fixtures/debugging/scripted_debugging.mp4"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/recovery"))
    options = parser.parse_args()
    print(json.dumps(verify(options.video, options.out), indent=2))
