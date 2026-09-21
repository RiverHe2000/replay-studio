"""Quiesced database AND object backup/restore, with integrity verification.

Stop API and every worker before either command. A database-only backup cannot
restore the source media, visual indexes or rendered exports stored in S3.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

from replay_studio.config import Config
from replay_studio.storage import Storage


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe(base: Path, relative: str) -> Path:
    destination = (base / relative).resolve()
    if not destination.is_relative_to(base.resolve()) or destination == base.resolve():
        raise ValueError("backup contains an unsafe file path")
    return destination


def pg_env(url: str) -> dict[str, str]:
    parsed = make_url(url)
    env = os.environ.copy()
    env.update(
        PGHOST=parsed.host or "localhost",
        PGPORT=str(parsed.port or 5432),
        PGDATABASE=parsed.database or "replay",
        PGUSER=parsed.username or "postgres",
    )
    if parsed.password:
        env["PGPASSWORD"] = parsed.password
    if "sslmode" in parsed.query:
        env["PGSSLMODE"] = str(parsed.query["sslmode"])
    return env


def pg_run(arguments: list[str], database_url: str) -> None:
    result = subprocess.run(arguments, env=pg_env(database_url), timeout=1800, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{arguments[0]} failed: {result.stderr[-2000:]}")


def backup(config: Config, destination: Path) -> dict:
    destination = destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("backup destination must be empty")
    # Backing up into the source tree recursively is never allowed.
    if destination.is_relative_to(config.data_dir.resolve()):
        raise ValueError("backup destination cannot be inside the live data directory")
    destination.mkdir(parents=True, exist_ok=True)
    url = make_url(config.database_url)
    if url.get_backend_name() == "sqlite":
        source = Path(url.database or "")
        if not source.is_file():
            raise ValueError("source SQLite database does not exist")
        with (
            sqlite3.connect(source) as source_db,
            sqlite3.connect(destination / "database.sqlite3") as target_db,
        ):
            source_db.backup(target_db)
        database_kind = "sqlite"
    elif url.get_backend_name() == "postgresql":
        pg_run(
            ["pg_dump", "--format=custom", "--no-owner", "--file", str(destination / "database.dump")],
            config.database_url,
        )
        database_kind = "postgresql"
    else:
        raise ValueError("only SQLite and PostgreSQL are supported")
    storage = Storage(config)
    objects = destination / "objects"
    objects.mkdir(exist_ok=True)
    if storage.s3 is not None:
        pages = storage.s3.get_paginator("list_objects_v2").paginate(Bucket=config.s3_bucket)
        for page in pages:
            for item in page.get("Contents", []):
                target = safe(objects, item["Key"])
                target.parent.mkdir(parents=True, exist_ok=True)
                storage.s3.download_file(config.s3_bucket, item["Key"], str(target))
    else:
        for source in storage.root.rglob("*"):
            if source.is_symlink():
                raise ValueError("symlinks are not accepted in object backups")
            if source.is_file():
                target = safe(objects, source.relative_to(storage.root).as_posix())
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    # In-progress resumable upload buffers are state too, when present.
    upload_root = config.data_dir / "uploads"
    if upload_root.is_dir():
        for source in upload_root.rglob("*"):
            if source.is_symlink():
                raise ValueError("symlinks are not accepted in upload backups")
            if source.is_file():
                target = safe(destination / "uploads", source.relative_to(upload_root).as_posix())
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
    manifest = {
        "format": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "database": database_kind,
        "storage_source": config.storage_backend,
        "quiesced": True,
        "files": [
            {
                "path": item.relative_to(destination).as_posix(),
                "size": item.stat().st_size,
                "sha256": digest(item),
            }
            for item in sorted(destination.rglob("*"))
            if item.is_file()
        ],
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "files": len(manifest["files"]),
        "bytes": sum(x["size"] for x in manifest["files"]),
        "database": database_kind,
    }


def verify(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1 or manifest.get("database") not in {"sqlite", "postgresql"}:
        raise ValueError("unsupported backup format")
    names = [item["path"] for item in manifest["files"]]
    if len(names) != len(set(names)):
        raise ValueError("backup contains duplicate manifest paths")
    expected_db = "database.sqlite3" if manifest["database"] == "sqlite" else "database.dump"
    if expected_db not in names:
        raise ValueError("backup lacks its database")
    actual = {
        p.relative_to(directory).as_posix()
        for p in directory.rglob("*")
        if p.is_file() and p != directory / "manifest.json"
    }
    if actual != set(names):
        raise ValueError("backup files do not match the manifest")
    for item in manifest["files"]:
        target = safe(directory, item["path"])
        if target.is_symlink() or target.stat().st_size != item["size"] or digest(target) != item["sha256"]:
            raise ValueError(f"backup integrity check failed: {item['path']}")
    return manifest


def restore(config: Config, directory: Path) -> dict:
    manifest = verify(directory)
    url = make_url(config.database_url)
    target_kind = url.get_backend_name()
    if target_kind != manifest["database"]:
        raise ValueError("source and target database types must match")
    storage = Storage(config)
    if storage.s3 is not None:
        if storage.s3.list_objects_v2(Bucket=config.s3_bucket, MaxKeys=1).get("KeyCount", 0):
            raise ValueError("target S3 bucket must be empty")
    elif any(storage.root.iterdir()):
        raise ValueError("target object directory must be empty")
    upload_root = config.data_dir / "uploads"
    if upload_root.exists() and any(upload_root.iterdir()):
        raise ValueError("target upload directory must be empty")
    if target_kind == "sqlite":
        database = Path(url.database or "")
        if database.exists():
            raise ValueError("target SQLite database must not exist")
    else:
        engine = create_engine(config.database_url)
        try:
            if inspect(engine).get_table_names():
                raise ValueError("target Postgres database must be empty")
        finally:
            engine.dispose()
    # Restore media first. If interrupted, leave applications stopped; never serve a
    # database whose required objects have not been copied and verified.
    for item in manifest["files"]:
        path = item["path"]
        if path.startswith("objects/"):
            storage.put_file(path[len("objects/") :], safe(directory, path))
        elif path.startswith("uploads/"):
            target = safe(upload_root, path[len("uploads/") :])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe(directory, path), target)
    if target_kind == "sqlite":
        database.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(directory / "database.sqlite3", database)
    else:
        pg_run(
            [
                "pg_restore",
                "--dbname",
                str(url.database),
                "--no-owner",
                "--exit-on-error",
                "--single-transaction",
                str(directory / "database.dump"),
            ],
            config.database_url,
        )
    return {"restored_files": len(manifest["files"]), "database": target_kind}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore", "verify"])
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--confirm-quiesced", action="store_true", help="API and all workers are stopped; no writes occur"
    )
    args = parser.parse_args()
    if args.action != "verify" and not args.confirm_quiesced:
        parser.error("stop API and ALL workers, then pass --confirm-quiesced")
    if args.action == "verify":
        result = verify(args.directory)
        print(json.dumps({"verified_files": len(result["files"])}))
    else:
        operation = backup if args.action == "backup" else restore
        print(json.dumps(operation(Config.from_env(), args.directory)))
