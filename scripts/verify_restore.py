"""Verify a restored installation's objects, manifest, retrieval and real rendering.

Run against an isolated restored target, before resuming its workers. Optional
REPLAY_VERIFY_EMAIL/PASSWORD exercise restored login, media and export HTTP routes.
The generated probe export is a local artifact, not a saved timeline edit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from backup import digest, verify
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select

from replay_studio import db, media, retrieval
from replay_studio.config import Config
from replay_studio.storage import Storage
from replay_studio.store import Store


def counts(url: str) -> dict[str, int]:
    engine = create_engine(url)
    try:
        tables = inspect(engine).get_table_names()
        with engine.connect() as connection:
            return {
                name: int(
                    connection.execute(
                        select(func.count()).select_from(Table(name, MetaData(), autoload_with=engine))
                    ).scalar_one()
                )
                for name in tables
            }
    finally:
        engine.dispose()


def probe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,codec_name",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(result.stdout)


def functional(config: Config, out: Path) -> dict:
    """Use a ready run from the target; do not modify its timeline or job queue."""
    # Force S3 reads from a fresh cache: restore already populated its own cache.
    check_config = (
        replace(config, data_dir=out / "readback-cache") if config.storage_backend == "s3" else config
    )
    store = Store(check_config)
    with store.transaction(False) as conn:
        asset = (
            conn.execute(
                select(db.assets)
                .join(db.runs, db.runs.c.id == db.assets.c.run_id)
                .where(~db.assets.c.deleted, db.runs.c.status == "ready")
                .limit(1)
            )
            .mappings()
            .first()
        )
        if asset is None:
            raise ValueError("functional restore verification needs a ready, non-deleted analyzed video")
        asset = dict(asset)
        existing_export = (
            conn.execute(
                select(db.exports)
                .where(db.exports.c.asset_id == asset["id"], db.exports.c.status == "ready")
                .limit(1)
            )
            .mappings()
            .first()
        )
    manifest, base = store.complete_manifest(asset["run_id"])
    original = store.storage.local_path(asset["source_key"])
    if digest(original) != asset["sha256"]:
        raise AssertionError("restored original media differs from its database checksum")
    segments = [*manifest.get("asr", {}).get("segments", []), *manifest.get("ocr", {}).get("segments", [])]
    text_segment = next((item for item in segments if str(item.get("text", "")).strip()), None)
    if text_segment is None:
        raise ValueError("functional search verification needs real transcript or OCR evidence")
    query = " ".join(str(text_segment["text"]).split()[:12])
    hits = retrieval.search(query, manifest, base, mode="speech_ocr")
    if not hits:
        raise AssertionError("restored speech/OCR index could not retrieve its own evidence")
    proxy = base / manifest["prepare"]["proxy"]
    render_dir = out / "rerender"
    render_dir.mkdir(parents=True, exist_ok=False)
    rendered = media.render(
        proxy,
        [
            {
                "id": "restore-probe",
                "start": 0.0,
                "end": min(3.0, manifest["prepare"]["duration"]),
                "title": "Restore verification",
                "subtitle": "Restored media export",
            }
        ],
        render_dir,
    )
    result = {
        "asset_id": asset["id"],
        "run_id": asset["run_id"],
        "original_sha256_verified": True,
        "source_probe": probe(proxy),
        "query": query,
        "retrieval_hits": len(hits),
        "rerender_probe": probe(render_dir / rendered["video"]),
        "http_login_media_export_verified": False,
    }
    if existing_export:
        key = existing_export["result"]["artifact_prefix"] + "/" + existing_export["result"]["video"]
        result["existing_export_probe"] = probe(store.storage.local_path(key))
    email, password = os.getenv("REPLAY_VERIFY_EMAIL"), os.getenv("REPLAY_VERIFY_PASSWORD")
    if email and password:
        from fastapi.testclient import TestClient

        from replay_studio.api import create_app

        with TestClient(create_app(check_config)) as client:
            response = client.post("/api/auth/login", json={"email": email, "password": password})
            response.raise_for_status()
            response = client.get(f"/api/assets/{asset['id']}/media", headers={"Range": "bytes=0-1023"})
            if response.status_code != 206 or len(response.content) != 1024:
                raise AssertionError("restored HTTP media range did not return 1024 bytes")
            if existing_export:
                response = client.get(f"/api/exports/{existing_export['id']}/video")
                response.raise_for_status()
                if hashlib.sha256(response.content).hexdigest() != digest(store.storage.local_path(key)):
                    raise AssertionError("restored HTTP export differs from its stored artifact")
            result["http_login_media_export_verified"] = bool(existing_export)
    store.engine.dispose()
    return result


def run(config: Config, snapshot: Path, out: Path) -> dict:
    if out.exists() and any(out.iterdir()):
        raise ValueError("verification output must be empty")
    out.mkdir(parents=True, exist_ok=True)
    manifest = verify(snapshot)
    source_url = os.getenv("REPLAY_VERIFY_SOURCE_DATABASE_URL")
    table_counts = counts(config.database_url)
    if source_url and counts(source_url) != table_counts:
        raise AssertionError("source and restored database table counts differ")
    check_config = (
        replace(config, data_dir=out / "object-readback") if config.storage_backend == "s3" else config
    )
    storage = Storage(check_config)
    verified_objects = 0
    for item in manifest["files"]:
        if item["path"].startswith("objects/"):
            key = item["path"][len("objects/") :]
            if digest(storage.local_path(key)) != item["sha256"]:
                raise AssertionError(f"restored object hash differs: {key}")
            verified_objects += 1
    report = {
        "status": "pass",
        "profile": f"{'sqlite' if config.database_url.startswith('sqlite:') else 'postgresql'}/{config.storage_backend}",
        "finished_at": datetime.now(UTC).isoformat(),
        "table_counts": table_counts,
        "source_table_counts_compared": bool(source_url),
        "verified_objects": verified_objects,
        "functional": functional(config, out),
    }
    (out / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--out", type=Path, default=Path("artifacts/restore-verification"))
    args = parser.parse_args()
    print(json.dumps(run(Config.from_env(), args.snapshot, args.out), indent=2))
