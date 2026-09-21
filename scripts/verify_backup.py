"""Restore a quiesced local installation and compare database counts and object hashes.

This proves the SQLite/local profile only. PostgreSQL/S3 restore drills must be
performed against separately provisioned empty target services.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from backup import backup, digest, restore, verify
from sqlalchemy import MetaData, Table, create_engine, func, inspect, select
from verify_restore import functional

from replay_studio.config import Config
from replay_studio.storage import Storage


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


def run(config: Config, out: Path) -> dict:
    if not config.database_url.startswith("sqlite:") or config.storage_backend != "local":
        raise ValueError("this automatic drill is limited to SQLite/local; use backup.py for Postgres/S3")
    out.mkdir(parents=True, exist_ok=True)
    snapshot = out / "snapshot"
    restored = Config(data_dir=out / "restored")
    backup_summary = backup(config, snapshot)
    manifest = verify(snapshot)
    restore_summary = restore(restored, snapshot)
    original_counts, restored_counts = counts(config.database_url), counts(restored.database_url)
    if original_counts != restored_counts:
        raise AssertionError("restored database row counts differ")
    storage = Storage(restored)
    objects = 0
    for item in manifest["files"]:
        if item["path"].startswith("objects/"):
            key = item["path"][len("objects/") :]
            if digest(storage.local_path(key)) != item["sha256"]:
                raise AssertionError("restored object checksum differs")
            objects += 1
    report = {
        "status": "pass",
        "profile": "sqlite/local",
        "finished_at": datetime.now(UTC).isoformat(),
        "backup": backup_summary,
        "restore": restore_summary,
        "verified_objects": objects,
        "table_counts": restored_counts,
        "postgres_s3_verified": False,
        "functional": functional(restored, out),
    }
    (out / "verification.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("artifacts/backup-drill"))
    parser.add_argument("--confirm-quiesced", action="store_true")
    args = parser.parse_args()
    if not args.confirm_quiesced:
        parser.error("stop source API and workers, then pass --confirm-quiesced")
    print(json.dumps(run(Config.from_env(), args.out), indent=2))
