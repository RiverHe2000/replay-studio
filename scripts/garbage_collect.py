"""Offline physical collection of logically deleted assets; dry-run by default.

Stop API and every worker. Wait at least one lease interval after the last worker
heartbeat. Database audit records are deliberately retained.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from replay_studio import db
from replay_studio.config import Config
from replay_studio.store import Store

IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")


def ident(value: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("unsafe database identifier; collection refused")
    return value


def safe_target(root: Path, relative: str) -> Path:
    root = root.resolve()
    lexical = root / relative
    target = lexical.resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError("collection target is outside the configured data directory")
    current = lexical
    while current != root:
        if current.is_symlink() or current.is_junction():
            raise ValueError("collection target traverses a link or junction")
        current = current.parent
    if target.is_dir():
        for base, directories, files in os.walk(target, followlinks=False):
            for name in directories + files:
                item = Path(base) / name
                if item.is_symlink() or item.is_junction():
                    raise ValueError("collection subtree contains a link or junction")
    return target


def artifact_references(value) -> set[str]:
    result = set()
    if isinstance(value, dict):
        if isinstance(value.get("artifact_prefix"), str):
            result.add(value["artifact_prefix"])
        for child in value.values():
            result.update(artifact_references(child))
    elif isinstance(value, list):
        for child in value:
            result.update(artifact_references(child))
    return result


def collect(config: Config, *, apply: bool = False, confirm_quiesced: bool = False) -> dict:
    if apply and not confirm_quiesced:
        raise ValueError("--apply requires --confirm-quiesced after stopping API and workers")
    store = Store(config)
    report = {
        "mode": "apply" if apply else "dry-run",
        "generated_at": datetime.now(UTC).isoformat(),
        "data_dir": str(config.data_dir),
        "audit_records_deleted": False,
    }
    with store.transaction() as conn:
        timestamp = time.time()
        active_jobs = (
            conn.execute(
                select(db.jobs.c.id).where(db.jobs.c.status == "running", db.jobs.c.lease_until > timestamp)
            )
            .scalars()
            .all()
        )
        recent_workers = (
            conn.execute(select(db.workers.c.id).where(db.workers.c.seen > timestamp - config.lease_seconds))
            .scalars()
            .all()
        )
        active_resources = (
            conn.execute(
                select(db.resources.c.id).where(
                    db.resources.c.token.is_not(None), db.resources.c.expires > timestamp
                )
            )
            .scalars()
            .all()
        )
        blockers = {
            "active_jobs": list(active_jobs),
            "recent_workers": list(recent_workers),
            "active_resource_leases": list(active_resources),
        }
        report["blockers"] = blockers
        if apply and any(blockers.values()):
            raise ValueError("active jobs, recent workers or resource leases exist; wait for quiescence")
        deleted_assets = (
            conn.execute(select(db.assets.c.id).where(db.assets.c.deleted).with_for_update()).scalars().all()
        )
        prefixes: set[str] = set()
        local_relatives: set[str] = set()
        for asset_id in deleted_assets:
            asset_id = ident(asset_id)
            prefixes.add(f"assets/{asset_id}")
            upload_ids = (
                conn.execute(select(db.uploads.c.id).where(db.uploads.c.asset_id == asset_id)).scalars().all()
            )
            for upload_id in upload_ids:
                local_relatives.add(f"uploads/{ident(upload_id)}.part")
            run_ids = conn.execute(select(db.runs.c.id).where(db.runs.c.asset_id == asset_id)).scalars().all()
            job_ids = conn.execute(select(db.jobs.c.id).where(db.jobs.c.asset_id == asset_id)).scalars().all()
            export_ids = (
                conn.execute(select(db.exports.c.id).where(db.exports.c.asset_id == asset_id)).scalars().all()
            )
            for run_id in run_ids:
                run_id = ident(run_id)
                prefixes.add(f"runs/{run_id}")
                local_relatives.add(f"materialized/{run_id}")
            for job_id in job_ids:
                local_relatives.add(f"work/{ident(job_id)}")
            for export_id in export_ids:
                prefixes.add(f"exports/{ident(export_id)}")
        live_references: set[str] = set()
        for table, column in (
            (db.runs, db.runs.c.manifest),
            (db.jobs, db.jobs.c.result),
            (db.exports, db.exports.c.result),
        ):
            for result in conn.execute(
                select(column).join(db.assets, db.assets.c.id == table.c.asset_id).where(~db.assets.c.deleted)
            ).scalars():
                live_references.update(artifact_references(result))
        for prefix in prefixes:
            if any(ref == prefix or ref.startswith(prefix + "/") for ref in live_references):
                raise ValueError("a live asset references a collection candidate; collection refused")
            local_relatives.add(store.storage.root.relative_to(config.data_dir).as_posix() + "/" + prefix)
        targets = [(relative, safe_target(config.data_dir, relative)) for relative in sorted(local_relatives)]
        report.update(
            asset_ids=list(deleted_assets),
            object_prefixes=sorted(prefixes),
            local_targets=[{"path": relative, "exists": path.exists()} for relative, path in targets],
        )
        if apply:
            # Re-check immediately before object deletion. The operator assertion
            # remains required: this script does not stop external processes.
            if conn.execute(
                select(db.jobs.c.id).where(db.jobs.c.status == "running", db.jobs.c.lease_until > time.time())
            ).first():
                raise ValueError("a worker became active during collection planning")
            for prefix in sorted(prefixes):
                safe_target(
                    config.data_dir, store.storage.root.relative_to(config.data_dir).as_posix() + "/" + prefix
                )
                store.storage.delete_prefix(prefix)
            for relative, _ in targets:
                target = safe_target(config.data_dir, relative)
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.is_file():
                    target.unlink()
        report["status"] = "collected" if apply else "planned"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-quiesced", action="store_true")
    parser.add_argument("--report", type=Path)
    options = parser.parse_args()
    result = collect(Config.from_env(), apply=options.apply, confirm_quiesced=options.confirm_quiesced)
    text = json.dumps(result, indent=2)
    if options.report:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(text, encoding="utf-8")
    print(text)
