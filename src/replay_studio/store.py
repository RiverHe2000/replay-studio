"""Transactional application state, fenced DAG execution, and immutable edit versions.

SQLite is a portable single-host profile. Postgres uses row locks for the deployed
profile. Neither a model nor a browser is trusted to authorize state transitions.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.metadata
import json
import math
import os
import secrets
import shutil
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import and_, create_engine, delete, event, func, insert, inspect, or_, select, update

from . import db
from .config import Config
from .storage import Storage


class StoreError(Exception):
    status = 400


class NotFound(StoreError):
    status = 404


class Forbidden(StoreError):
    status = 403


class Conflict(StoreError):
    status = 409


class Unauthorized(StoreError):
    status = 401


class TooManyRequests(StoreError):
    status = 429


def uid() -> str:
    return uuid.uuid4().hex


def now() -> str:
    return datetime.now(UTC).isoformat()


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def one(conn, stmt, message="Not found") -> dict:
    row = conn.execute(stmt).mappings().first()
    if row is None:
        raise NotFound(message)
    return dict(row)


def all_rows(conn, stmt) -> list[dict]:
    return [dict(row) for row in conn.execute(stmt).mappings()]


def public_user(user: dict) -> dict:
    return {key: user[key] for key in ("id", "email", "name")}


def public_asset(asset: dict) -> dict:
    return {key: asset[key] for key in ("id", "project_id", "name", "size", "duration", "status", "run_id", "created_at")}


def unavailable(result: dict, settings: dict | None = None) -> bool:
    return result.get("status") == "unavailable" or bool(
        (settings or {}).get("vlm_model") and (result.get("vlm") or {}).get("status") == "unavailable"
    )


class Store:
    def __init__(self, config: Config):
        self.config = config
        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage = Storage(config)
        self.sqlite = config.database_url.startswith("sqlite:")
        self.engine = create_engine(
            config.database_url, pool_pre_ping=True,
            connect_args={"check_same_thread": False, "timeout": 30} if self.sqlite else {},
        )
        if self.sqlite:
            @event.listens_for(self.engine, "connect")
            def pragmas(connection, _):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=30000")
        with self.transaction() as conn:
            if not self.sqlite:
                conn.exec_driver_sql("SELECT pg_advisory_xact_lock(720260919)")
            db.metadata.create_all(conn)
            version = conn.execute(select(db.schema_version.c.version)).scalar()
            if version is None:
                conn.execute(insert(db.schema_version).values(id=1, version=3))
            elif version == 1:
                columns = {c["name"] for c in inspect(conn).get_columns("comments")}
                if "asset_id" not in columns:
                    conn.exec_driver_sql("ALTER TABLE comments ADD COLUMN asset_id VARCHAR(32) REFERENCES assets(id)")
                conn.execute(update(db.schema_version).where(db.schema_version.c.id == 1).values(version=3))
            elif version == 2:
                conn.execute(update(db.schema_version).where(db.schema_version.c.id == 1).values(version=3))
            elif version != 3:
                raise RuntimeError(f"Unsupported schema version {version}; migrate before starting")
            for resource in ("gpu", "registration"):
                if conn.execute(select(db.resources.c.id).where(db.resources.c.id == resource)).scalar() is None:
                    conn.execute(insert(db.resources).values(id=resource, token=None, expires=0))

    @contextmanager
    def transaction(self, write=True):
        with self.engine.connect() as conn:
            if self.sqlite and write:
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                conn.begin()
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def role(self, conn, project_id: str, user_id: str, minimum="viewer") -> str:
        membership = conn.execute(select(db.members.c.role).where(
            db.members.c.project_id == project_id, db.members.c.user_id == user_id,
        )).scalar()
        if membership is None:
            raise NotFound("Project not found")
        if {"viewer": 0, "editor": 1, "owner": 2}[membership] < {"viewer": 0, "editor": 1, "owner": 2}[minimum]:
            raise Forbidden("Your project role does not permit this action")
        return membership

    def auth_status(self) -> dict:
        with self.transaction(False) as conn:
            return {"needs_setup": conn.execute(select(func.count()).select_from(db.users)).scalar() == 0,
                    "registration_open": self.config.registration_open}

    def auth_rate_limit(self, key: str, limit: int = 20, seconds: int = 60) -> None:
        """Shared DB-backed authentication limit, including separate API processes."""
        digest = hashlib.sha256(key.encode()).hexdigest()
        exceeded = False
        with self.transaction() as conn:
            one(conn, select(db.resources).where(db.resources.c.id == "registration").with_for_update())
            conn.execute(delete(db.auth_limits).where(db.auth_limits.c.started < time.time() - 3600))
            row = conn.execute(select(db.auth_limits).where(db.auth_limits.c.id == digest)).mappings().first()
            if row is None:
                conn.execute(insert(db.auth_limits).values(id=digest, started=time.time(), count=1))
            elif row["started"] + seconds <= time.time():
                conn.execute(update(db.auth_limits).where(db.auth_limits.c.id == digest).values(started=time.time(), count=1))
            elif row["count"] >= limit:
                exceeded = True
            else:
                conn.execute(update(db.auth_limits).where(db.auth_limits.c.id == digest).values(count=row["count"] + 1))
        if exceeded:
            raise TooManyRequests("Too many sign-in attempts. Wait a minute and try again.")

    def register(self, email: str, password: str, name: str) -> dict:
        email, name = email.strip().lower(), name.strip()
        if "@" not in email or len(email) > 254 or not 1 <= len(name) <= 80:
            raise StoreError("Provide a valid email and a name of 1–80 characters")
        if not 10 <= len(password) <= 256:
            raise StoreError("Password must contain 10–256 characters")
        salt = secrets.token_hex(16)
        password_hash = salt + ":" + hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()
        with self.transaction() as conn:
            one(conn, select(db.resources).where(db.resources.c.id == "registration").with_for_update())
            count = conn.execute(select(func.count()).select_from(db.users)).scalar()
            if count and not self.config.registration_open:
                raise Forbidden("Registration is closed. Ask the administrator to enable registration.")
            if conn.execute(select(db.users.c.id).where(db.users.c.email == email)).scalar():
                raise Conflict("This email is already registered")
            user = dict(id=uid(), email=email, name=name, password_hash=password_hash, created_at=now())
            conn.execute(insert(db.users).values(**user))
        return self.new_session(user)

    def login(self, email: str, password: str) -> dict:
        with self.transaction(False) as conn:
            row = conn.execute(select(db.users).where(db.users.c.email == email.strip().lower())).mappings().first()
        # Same KDF work for missing and existing users prevents a trivial timing oracle.
        stored = row["password_hash"] if row else ("00" * 16 + ":" + "00" * 32)
        salt, expected = stored.split(":")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()
        if not row or not hmac.compare_digest(actual, expected):
            raise Unauthorized("Incorrect email or password")
        return self.new_session(dict(row))

    def new_session(self, user: dict) -> dict:
        token, csrf = secrets.token_urlsafe(32), secrets.token_hex(32)
        with self.transaction() as conn:
            conn.execute(delete(db.sessions).where(db.sessions.c.expires <= time.time()))
            conn.execute(insert(db.sessions).values(token_hash=hashlib.sha256(token.encode()).hexdigest(),
                user_id=user["id"], csrf=csrf, expires=time.time() + self.config.session_hours * 3600))
        return {"user": public_user(user), "csrf_token": csrf, "token": token}

    def authenticate(self, token: str | None) -> dict:
        if not token or len(token) > 200:
            raise Unauthorized("Sign in to continue")
        with self.transaction(False) as conn:
            row = conn.execute(select(db.sessions, db.users.c.email, db.users.c.name).join(
                db.users, db.users.c.id == db.sessions.c.user_id,
            ).where(db.sessions.c.token_hash == hashlib.sha256(token.encode()).hexdigest(),
                    db.sessions.c.expires > time.time())).mappings().first()
        if row is None:
            raise Unauthorized("Session expired. Sign in again.")
        return {"user": {"id": row["user_id"], "email": row["email"], "name": row["name"]}, "csrf_token": row["csrf"]}

    def logout(self, token: str) -> None:
        with self.transaction() as conn:
            conn.execute(delete(db.sessions).where(db.sessions.c.token_hash == hashlib.sha256(token.encode()).hexdigest()))

    def create_project(self, user_id: str, name: str) -> dict:
        if not 1 <= len(name.strip()) <= 120:
            raise StoreError("Project name must contain 1–120 characters")
        project = dict(id=uid(), name=name.strip(), owner_id=user_id, version=0, created_at=now(), updated_at=now())
        with self.transaction() as conn:
            conn.execute(insert(db.projects).values(**project))
            conn.execute(insert(db.members).values(project_id=project["id"], user_id=user_id, role="owner"))
        return {**project, "role": "owner"}

    def list_projects(self, user_id: str) -> list[dict]:
        with self.transaction(False) as conn:
            return all_rows(conn, select(db.projects, db.members.c.role).join(db.members).where(
                db.members.c.user_id == user_id).order_by(db.projects.c.updated_at.desc()))

    @staticmethod
    def _timeline(conn, project_id: str, version: int) -> dict:
        if version == 0:
            return {"version": 0, "asset_id": None, "run_id": None, "clips": [], "updated_at": None}
        return one(conn, select(db.timelines).where(db.timelines.c.project_id == project_id, db.timelines.c.version == version))

    def project(self, project_id: str, user_id: str) -> dict:
        with self.transaction(False) as conn:
            role = self.role(conn, project_id, user_id)
            project = one(conn, select(db.projects).where(db.projects.c.id == project_id))
            asset_list = all_rows(conn, select(db.assets).where(db.assets.c.project_id == project_id, ~db.assets.c.deleted).order_by(db.assets.c.created_at))
            member_list = all_rows(conn, select(db.members.c.user_id, db.members.c.role, db.users.c.name, db.users.c.email).join(db.users).where(db.members.c.project_id == project_id))
            comment_list = all_rows(conn, select(db.comments, db.users.c.name).join(db.users).where(db.comments.c.project_id == project_id).order_by(db.comments.c.created_at))
            export_list = all_rows(conn, select(db.exports).join(db.assets, db.assets.c.id == db.exports.c.asset_id).where(db.exports.c.project_id == project_id, ~db.assets.c.deleted).order_by(db.exports.c.created_at.desc()))
            timeline = self._timeline(conn, project_id, project["version"])
        return {**project, "role": role, "assets": [public_asset(a) for a in asset_list], "timeline": timeline,
                "jobs": self.jobs(project_id), "exports": [{k: v for k, v in e.items() if k not in {"clips", "result"}} for e in export_list],
                "members": member_list, "comments": comment_list}

    def add_member(self, project_id: str, user_id: str, email: str, role: str) -> None:
        if role not in {"editor", "viewer"}:
            raise StoreError("Role must be editor or viewer")
        with self.transaction() as conn:
            self.role(conn, project_id, user_id, "owner")
            target = one(conn, select(db.users).where(db.users.c.email == email.strip().lower()), "This user must register first")
            existing = conn.execute(select(db.members.c.role).where(db.members.c.project_id == project_id, db.members.c.user_id == target["id"])).scalar()
            if existing == "owner":
                raise Conflict("The project owner role cannot be changed")
            if existing:
                conn.execute(update(db.members).where(db.members.c.project_id == project_id, db.members.c.user_id == target["id"]).values(role=role))
            else:
                conn.execute(insert(db.members).values(project_id=project_id, user_id=target["id"], role=role))

    def remove_member(self, project_id: str, user_id: str, target_id: str) -> None:
        with self.transaction() as conn:
            self.role(conn, project_id, user_id, "owner")
            if target_id == user_id:
                raise Conflict("The project owner cannot be removed")
            conn.execute(delete(db.members).where(db.members.c.project_id == project_id, db.members.c.user_id == target_id))

    def comment(self, project_id: str, user_id: str, text: str, timestamp: float | None, asset_id: str | None = None) -> dict:
        if not 1 <= len(text.strip()) <= 2000 or (timestamp is not None and (not math.isfinite(timestamp) or timestamp < 0 or timestamp > 1800)):
            raise StoreError("Invalid comment or media time")
        data = dict(id=uid(), project_id=project_id, user_id=user_id, asset_id=asset_id, text=text.strip(), time=timestamp, created_at=now())
        with self.transaction() as conn:
            self.role(conn, project_id, user_id)
            if timestamp is not None and asset_id is None:
                raise StoreError("A timestamped comment must identify its source video")
            if asset_id:
                asset = one(conn, select(db.assets).where(db.assets.c.id == asset_id, db.assets.c.project_id == project_id, ~db.assets.c.deleted))
                if timestamp is not None and (asset["duration"] is None or timestamp > asset["duration"]):
                    raise StoreError("Comment time is outside the source video")
            conn.execute(insert(db.comments).values(**data))
        return data

    def init_upload(self, project_id: str, user_id: str, filename: str, size: int) -> dict:
        filename = Path(filename.replace("\\", "/")).name
        if not filename or len(filename) > 240 or not 1 <= size <= self.config.max_upload_bytes:
            raise StoreError("File name or size exceeds the configured upload limit")
        with self.transaction() as conn:
            self.role(conn, project_id, user_id, "editor")
            one(conn, select(db.projects).where(db.projects.c.id == project_id).with_for_update())
            used = conn.execute(select(func.coalesce(func.sum(db.assets.c.size), 0)).where(db.assets.c.project_id == project_id, ~db.assets.c.deleted)).scalar()
            reserved = conn.execute(select(func.coalesce(func.sum(db.uploads.c.size), 0)).where(db.uploads.c.project_id == project_id, db.uploads.c.status == "uploading")).scalar()
            if used + reserved + size > self.config.project_quota_bytes:
                raise StoreError("Project storage quota exceeded")
            if shutil.disk_usage(self.config.data_dir).free < size + 100 * 1024 * 1024:
                raise StoreError("Insufficient local disk space for this upload")
            data = dict(id=uid(), project_id=project_id, user_id=user_id, filename=filename, size=size, offset=0, status="uploading", asset_id=None, created_at=now())
            conn.execute(insert(db.uploads).values(**data))
        return {**data, "chunk_size": self.config.chunk_size, "max_size": self.config.max_upload_bytes}

    def _upload(self, conn, upload_id: str, user_id: str, lock=False) -> dict:
        stmt = select(db.uploads).where(db.uploads.c.id == upload_id)
        data = one(conn, stmt.with_for_update() if lock else stmt, "Upload not found")
        self.role(conn, data["project_id"], user_id, "editor")
        if data["user_id"] != user_id:
            raise NotFound("Upload not found")
        return data

    def upload(self, upload_id: str, user_id: str) -> dict:
        with self.transaction(False) as conn:
            return self._upload(conn, upload_id, user_id)

    def cancel_upload(self, upload_id: str, user_id: str) -> None:
        """Fence concurrent appends before releasing staging bytes and quota."""
        with self.transaction() as conn:
            upload = self._upload(conn, upload_id, user_id, True)
            if upload["status"] == "complete":
                raise Conflict("Upload is complete; remove its asset instead")
            conn.execute(update(db.uploads).where(db.uploads.c.id == upload_id).values(status="cancelled"))
        self._upload_path(upload_id).unlink(missing_ok=True)

    def _upload_path(self, upload_id: str) -> Path:
        root = self.config.data_dir / "uploads"
        root.mkdir(exist_ok=True)
        return root / (upload_id + ".part")

    def append_upload(self, upload_id: str, user_id: str, offset: int, content: bytes) -> dict:
        if not content or len(content) > self.config.chunk_size or offset < 0:
            raise StoreError("Invalid upload chunk")
        with self.transaction() as conn:
            upload = self._upload(conn, upload_id, user_id, True)
            if upload["status"] != "uploading":
                raise Conflict("Upload is already completed")
            if offset + len(content) > upload["size"] or offset > upload["offset"]:
                raise Conflict("Chunk offset does not match upload progress")
            path = self._upload_path(upload_id)
            if offset < upload["offset"]:
                if offset + len(content) > upload["offset"] or not path.exists():
                    raise Conflict("Overlapping chunk does not match committed progress")
                with path.open("rb") as stream:
                    stream.seek(offset)
                    if stream.read(len(content)) != content:
                        raise Conflict("Replayed chunk differs from original bytes")
                return upload
            with path.open("r+b" if path.exists() else "w+b") as stream:
                stream.seek(0, 2)
                if stream.tell() < upload["offset"]:
                    raise Conflict("Upload staging file is incomplete; restart upload")
                stream.truncate(upload["offset"])
                stream.seek(offset)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            upload["offset"] += len(content)
            conn.execute(update(db.uploads).where(db.uploads.c.id == upload_id).values(offset=upload["offset"]))
            return upload

    def complete_upload(self, upload_id: str, user_id: str) -> dict:
        with self.transaction() as conn:
            upload = self._upload(conn, upload_id, user_id, True)
            if upload["asset_id"]:
                asset = one(conn, select(db.assets).where(db.assets.c.id == upload["asset_id"], ~db.assets.c.deleted))
                return public_asset(asset)
            if upload["status"] != "uploading":
                raise Conflict("Upload was cancelled")
            if upload["offset"] != upload["size"]:
                raise Conflict("Upload is incomplete")
            path = self._upload_path(upload_id)
            if not path.exists() or path.stat().st_size != upload["size"]:
                raise Conflict("Upload size verification failed")
            with path.open("rb") as stream:
                sha = hashlib.file_digest(stream, "sha256").hexdigest()
            asset_id = upload_id
            source_key = f"assets/{asset_id}/source"
            self.storage.put_file(source_key, path)
            asset = dict(id=asset_id, project_id=upload["project_id"], name=upload["filename"], size=upload["size"], sha256=sha,
                         source_key=source_key, duration=None, status="queued", run_id=None, deleted=False, created_at=now())
            conn.execute(insert(db.assets).values(**asset))
            run = self._create_analysis(conn, asset, {})
            asset["run_id"] = run["id"]
            conn.execute(update(db.uploads).where(db.uploads.c.id == upload_id).values(status="complete", asset_id=asset_id))
        path.unlink(missing_ok=True)
        return public_asset(asset)

    def asset(self, asset_id: str, user_id: str | None = None, minimum="viewer") -> dict:
        with self.transaction(False) as conn:
            data = one(conn, select(db.assets).where(db.assets.c.id == asset_id, ~db.assets.c.deleted), "Asset not found")
            if user_id:
                self.role(conn, data["project_id"], user_id, minimum)
            return data

    def delete_asset(self, asset_id: str, user_id: str) -> None:
        with self.transaction() as conn:
            asset = one(conn, select(db.assets).where(db.assets.c.id == asset_id, ~db.assets.c.deleted).with_for_update())
            self.role(conn, asset["project_id"], user_id, "editor")
            conn.execute(update(db.assets).where(db.assets.c.id == asset_id).values(deleted=True, status="deleted"))
            conn.execute(update(db.jobs).where(db.jobs.c.asset_id == asset_id, db.jobs.c.status.in_(["queued", "running", "blocked"])).values(status="cancelled", updated_at=now(), error="Asset deleted"))
            conn.execute(update(db.runs).where(db.runs.c.asset_id == asset_id).values(status="deleted"))
            conn.execute(update(db.exports).where(db.exports.c.asset_id == asset_id).values(status="deleted"))
        # Access is revoked transactionally. Physical collection is an explicit offline
        # operation so a still-cancelling worker cannot resurrect a deleted object set.

    @staticmethod
    def _job(conn, project_id, asset_id, run_id, kind, queue, dependencies, payload) -> dict:
        job = dict(id=uid(), project_id=project_id, asset_id=asset_id, run_id=run_id, kind=kind, queue=queue,
                   dependencies=dependencies, payload=payload, status="queued", attempts=0, lease_token=None,
                   lease_until=None, worker_id=None, artifact_prefix=None, result=None, error=None,
                   created_at=now(), updated_at=now())
        conn.execute(insert(db.jobs).values(**job))
        return job

    def _create_analysis(self, conn, asset: dict, settings: dict) -> dict:
        config = {"asr": True, "ocr": True, "visual": True, **settings}
        config["pipeline_version"] = "2"
        config["asr_model"] = os.getenv("REPLAY_ASR_MODEL", "base")
        config["visual_model"] = os.getenv("REPLAY_VISUAL_MODEL", "openai/clip-vit-base-patch32")
        config["vlm_model"] = os.getenv("REPLAY_VLM_MODEL", "")
        for key in ("asr_revision", "visual_revision", "vlm_revision"):
            config[key] = os.getenv("REPLAY_" + key.upper(), "")
        config["asr_language"] = os.getenv("REPLAY_ASR_LANGUAGE", "")
        config["model_device"] = os.getenv("REPLAY_MODEL_DEVICE", "cpu")
        config["packages"] = {}
        for package in ("faster-whisper", "rapidocr", "transformers", "torch", "torchvision"):
            try:
                config["packages"][package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                config["packages"][package] = None
        fingerprint = hashlib.sha256((asset["sha256"] + canonical(config)).encode()).hexdigest()
        existing = conn.execute(select(db.runs).where(db.runs.c.asset_id == asset["id"], db.runs.c.fingerprint == fingerprint,
                          db.runs.c.status.in_(["queued", "running", "ready"])).order_by(db.runs.c.created_at.desc())).mappings().first()
        if existing and existing["status"] == "ready" and any(
            unavailable(result, config) for result in (existing["manifest"] or {}).values()
        ):
            existing = None
        if existing:
            conn.execute(update(db.assets).where(db.assets.c.id == asset["id"]).values(run_id=existing["id"], status=existing["status"]))
            return dict(existing)
        run = dict(id=uid(), project_id=asset["project_id"], asset_id=asset["id"], fingerprint=fingerprint,
                   config=config, status="queued", manifest=None, created_at=now())
        conn.execute(insert(db.runs).values(**run))
        prepare = self._analysis_job(conn, asset, run, "prepare", "cpu", [])
        stages = [self._analysis_job(conn, asset, run, kind, queue, [prepare["id"]])
                  for kind, queue in [("asr", "gpu"), ("ocr", "cpu"), ("visual", "gpu")]]
        self._job(conn, asset["project_id"], asset["id"], run["id"], "index", "cpu", [stage["id"] for stage in stages], {})
        conn.execute(update(db.assets).where(db.assets.c.id == asset["id"]).values(run_id=run["id"], status="queued"))
        return run

    def _analysis_job(self, conn, asset: dict, run: dict, kind: str, queue: str, dependencies: list) -> dict:
        config = run["config"]
        identity = {"source": asset["sha256"], "pipeline": config["pipeline_version"], "stage": kind}
        if kind != "prepare":
            identity["enabled"] = config[kind]
            identity["device"] = config["model_device"]
        if kind == "asr":
            identity.update(model=config["asr_model"], revision=config["asr_revision"], language=config["asr_language"], package=config["packages"]["faster-whisper"])
        elif kind == "ocr":
            identity["package"] = config["packages"]["rapidocr"]
        elif kind == "visual":
            identity.update(model=config["visual_model"], vlm=config["vlm_model"], visual_revision=config["visual_revision"], vlm_revision=config["vlm_revision"], packages={p: config["packages"][p] for p in ("torch", "torchvision", "transformers")})
        cache_key = hashlib.sha256(canonical(identity).encode()).hexdigest()
        job = self._job(conn, asset["project_id"], asset["id"], run["id"], kind, queue, dependencies, {"cache_key": cache_key})
        candidates = all_rows(conn, select(db.jobs).where(db.jobs.c.asset_id == asset["id"], db.jobs.c.kind == kind,
                             db.jobs.c.status == "complete").order_by(db.jobs.c.created_at.desc()))
        for previous in candidates:
            if previous["payload"].get("cache_key") != cache_key or not previous["result"]:
                continue
            # Unavailable backends can become available later; never cache their failures.
            if unavailable(previous["result"], config):
                continue
            result = {**previous["result"], "cache_hit": True, "reused_from_job": previous["id"]}
            conn.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(
                status="complete", result=result, artifact_prefix=previous["artifact_prefix"], updated_at=now()))
            job.update(status="complete", result=result, artifact_prefix=previous["artifact_prefix"])
            break
        return job

    def create_analysis(self, project_id: str, asset_id: str, config: dict | None = None) -> dict:
        with self.transaction() as conn:
            asset = one(conn, select(db.assets).where(db.assets.c.id == asset_id, db.assets.c.project_id == project_id, ~db.assets.c.deleted).with_for_update())
            return self._create_analysis(conn, asset, config or {})

    def _propagate(self, conn) -> None:
        """Transition terminal dependencies and exhausted expired leases explicitly."""
        expired = all_rows(conn, select(db.jobs).where(db.jobs.c.status == "running", db.jobs.c.lease_until <= time.time(), db.jobs.c.attempts >= self.config.max_attempts).with_for_update(skip_locked=True))
        for job in expired:
            changed = conn.execute(update(db.jobs).where(db.jobs.c.id == job["id"], db.jobs.c.status == "running",
                db.jobs.c.lease_token == job["lease_token"], db.jobs.c.lease_until <= time.time()).values(
                    status="failed", error="Worker lease expired; retry budget exhausted", updated_at=now())).rowcount
            if changed:
                self._mark_failure(conn, job, "failed")
        # Two passes suffice for this bounded DAG (prepare -> analyses -> index).
        for _ in range(3):
            states = dict(conn.execute(select(db.jobs.c.id, db.jobs.c.status)).all())
            for job in all_rows(conn, select(db.jobs).where(db.jobs.c.status == "queued").with_for_update(skip_locked=True)):
                if any(states.get(dep) in {"failed", "cancelled", "blocked"} for dep in job["dependencies"]):
                    conn.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(status="blocked", error="A required stage did not complete", updated_at=now()))
                    self._mark_failure(conn, job, "failed")

    def claim(self, queue: str, worker_id: str) -> dict | None:
        if queue not in {"all", "cpu", "gpu"}:
            raise ValueError("Invalid worker queue")
        with self.transaction() as conn:
            self._propagate(conn)
            query = select(db.jobs).where(or_(db.jobs.c.status == "queued", and_(db.jobs.c.status == "running", db.jobs.c.lease_until <= time.time())), db.jobs.c.attempts < self.config.max_attempts)
            if queue != "all":
                query = query.where(db.jobs.c.queue == queue)
            # FIFO with bounded retries; failed attempts move behind older queued work.
            candidates = all_rows(conn, query.order_by(db.jobs.c.updated_at, db.jobs.c.created_at).with_for_update(skip_locked=True))
            states = dict(conn.execute(select(db.jobs.c.id, db.jobs.c.status)).all())
            for job in candidates:
                if any(states.get(dep) != "complete" for dep in job["dependencies"]):
                    continue
                asset = one(conn, select(db.assets).where(db.assets.c.id == job["asset_id"]))
                if asset["deleted"]:
                    conn.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(status="cancelled"))
                    continue
                token = uid()
                until = time.time() + self.config.lease_seconds
                if job["queue"] == "gpu":
                    acquired = conn.execute(update(db.resources).where(db.resources.c.id == "gpu", db.resources.c.expires <= time.time()).values(token=token, expires=until)).rowcount
                    if not acquired:
                        continue
                job.update(status="running", attempts=job["attempts"] + 1, lease_token=token, lease_until=until,
                           worker_id=worker_id, artifact_prefix=f"runs/{job['run_id'] or job['id']}/attempts/{job['id']}/{token}", updated_at=now(), error=None)
                conn.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(**{k: job[k] for k in ("status", "attempts", "lease_token", "lease_until", "worker_id", "artifact_prefix", "updated_at", "error")}))
                if job["kind"] != "render":
                    conn.execute(update(db.runs).where(db.runs.c.id == job["run_id"]).values(status="running"))
                    conn.execute(update(db.assets).where(db.assets.c.id == job["asset_id"], db.assets.c.run_id == job["run_id"]).values(status="processing"))
                else:
                    conn.execute(update(db.exports).where(db.exports.c.job_id == job["id"]).values(status="running"))
                return job
        return None

    def _live_job(self, conn, job_id: str, token: str) -> dict | None:
        row = conn.execute(select(db.jobs).where(db.jobs.c.id == job_id, db.jobs.c.lease_token == token,
            db.jobs.c.status == "running", db.jobs.c.lease_until > time.time()).with_for_update()).mappings().first()
        if row is None:
            return None
        asset = one(conn, select(db.assets).where(db.assets.c.id == row["asset_id"]))
        return None if asset["deleted"] else dict(row)

    def heartbeat(self, job_id: str, token: str) -> bool:
        with self.transaction() as conn:
            job = self._live_job(conn, job_id, token)
            if job is None:
                return False
            until = time.time() + self.config.lease_seconds
            if job["queue"] == "gpu":
                if not conn.execute(update(db.resources).where(db.resources.c.id == "gpu", db.resources.c.token == token, db.resources.c.expires > time.time()).values(expires=until)).rowcount:
                    return False
            conn.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(lease_until=until, updated_at=now()))
            return True

    def cancelled(self, job_id: str, token: str) -> bool:
        with self.transaction(False) as conn:
            row = conn.execute(select(db.jobs.c.status, db.jobs.c.lease_token, db.jobs.c.lease_until).where(db.jobs.c.id == job_id)).mappings().first()
            return row is None or row["status"] != "running" or row["lease_token"] != token or row["lease_until"] <= time.time()

    def abandon(self, job_id: str, token: str) -> bool:
        """A worker calls this only after computation has actually stopped.

        Cancellation may retain the token; manual retry may already clear it.
        The resource's token comparison never releases a replacement worker.
        """
        with self.transaction() as conn:
            job = conn.execute(select(db.jobs).where(db.jobs.c.id == job_id).with_for_update()).mappings().first()
            if job is None or job["queue"] != "gpu":
                return False
            if (job["status"] == "running" and job["lease_token"] == token
                    and (job["lease_until"] or 0) > time.time()):
                return False
            return bool(conn.execute(update(db.resources).where(
                db.resources.c.id == "gpu", db.resources.c.token == token,
            ).values(token=None, expires=0)).rowcount)

    @staticmethod
    def _release(conn, job: dict) -> None:
        if job["queue"] == "gpu":
            conn.execute(update(db.resources).where(db.resources.c.id == "gpu", db.resources.c.token == job["lease_token"]).values(token=None, expires=0))

    def finish(self, job_id: str, token: str, result: dict) -> bool:
        canonical(result)
        with self.transaction() as conn:
            job = self._live_job(conn, job_id, token)
            if job is None:
                return False
            result = {**result, "artifact_prefix": job["artifact_prefix"]}
            conn.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(status="complete", result=result, lease_until=None, updated_at=now()))
            self._release(conn, job)
            if job["kind"] == "prepare":
                duration = result.get("duration")
                if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
                    raise StoreError("Prepare stage returned an invalid duration")
                conn.execute(update(db.assets).where(db.assets.c.id == job["asset_id"]).values(duration=duration))
            if job["kind"] == "index":
                stages = all_rows(conn, select(db.jobs).where(db.jobs.c.run_id == job["run_id"], db.jobs.c.kind != "render"))
                manifest = {stage["kind"]: stage["result"] for stage in stages if stage["kind"] != "index"}
                if any(not manifest.get(name) for name in ("prepare", "asr", "ocr", "visual")):
                    raise Conflict("Analysis dependencies are incomplete")
                conn.execute(update(db.runs).where(db.runs.c.id == job["run_id"]).values(status="ready", manifest=manifest))
                conn.execute(update(db.assets).where(db.assets.c.id == job["asset_id"], db.assets.c.run_id == job["run_id"]).values(status="ready"))
            if job["kind"] == "render":
                conn.execute(update(db.exports).where(db.exports.c.job_id == job_id).values(status="ready", result=result))
            return True

    @staticmethod
    def _mark_failure(conn, job: dict, state: str) -> None:
        if job["kind"] == "render":
            conn.execute(update(db.exports).where(db.exports.c.job_id == job["id"]).values(status=state))
        else:
            conn.execute(update(db.runs).where(db.runs.c.id == job["run_id"]).values(status=state))
            conn.execute(update(db.assets).where(db.assets.c.id == job["asset_id"], db.assets.c.run_id == job["run_id"], ~db.assets.c.deleted).values(status=state))

    def fail(self, job_id: str, token: str, error: str) -> bool:
        with self.transaction() as conn:
            job = self._live_job(conn, job_id, token)
            if job is None:
                return False
            status = "failed" if job["attempts"] >= self.config.max_attempts else "queued"
            conn.execute(update(db.jobs).where(db.jobs.c.id == job_id).values(status=status, error=error[:1800], lease_until=None, updated_at=now()))
            self._release(conn, job)
            if status == "failed":
                self._mark_failure(conn, job, status)
            return True

    def change_job(self, job_id: str, user_id: str, action: str) -> dict:
        with self.transaction() as conn:
            job = one(conn, select(db.jobs).where(db.jobs.c.id == job_id).with_for_update())
            self.role(conn, job["project_id"], user_id, "editor")
            asset = one(conn, select(db.assets).where(db.assets.c.id == job["asset_id"], ~db.assets.c.deleted))
            if action == "cancel":
                if job["status"] == "complete":
                    raise Conflict("Completed work cannot be cancelled")
                # Cancel the entire analysis DAG, not unrelated exports using its snapshot.
                predicate = db.jobs.c.id == job_id if job["kind"] == "render" else and_(db.jobs.c.run_id == job["run_id"], db.jobs.c.kind != "render")
                for target in all_rows(conn, select(db.jobs).where(predicate, db.jobs.c.status.in_(["queued", "running", "blocked"]))):
                    conn.execute(update(db.jobs).where(db.jobs.c.id == target["id"]).values(status="cancelled", updated_at=now(), error="Cancelled by user"))
                    # Do not release a running GPU immediately: its lease fences a
                    # terminating process until heartbeat observes cancellation.
                self._mark_failure(conn, job, "cancelled")
            elif action == "retry":
                if job["status"] not in {"failed", "blocked", "cancelled"}:
                    raise Conflict("Only failed or cancelled jobs can be retried")
                predicate = db.jobs.c.id == job_id if job["kind"] == "render" else and_(db.jobs.c.run_id == job["run_id"], db.jobs.c.kind != "render")
                conn.execute(update(db.jobs).where(predicate, db.jobs.c.status.in_(["failed", "blocked", "cancelled"])).values(status="queued", attempts=0, lease_token=None, lease_until=None, error=None, updated_at=now()))
                if job["kind"] == "render":
                    conn.execute(update(db.exports).where(db.exports.c.job_id == job_id).values(status="queued"))
                else:
                    conn.execute(update(db.runs).where(db.runs.c.id == job["run_id"]).values(status="queued"))
                    conn.execute(update(db.assets).where(db.assets.c.id == asset["id"]).values(status="queued", run_id=job["run_id"]))
            return one(conn, select(db.jobs).where(db.jobs.c.id == job_id))

    def work_dir(self, job: dict) -> Path:
        path = self.config.data_dir / "work" / job["id"] / job["lease_token"]
        path.mkdir(parents=True, exist_ok=True)
        return path

    def publish_dir(self, job: dict, path: Path) -> None:
        if self.cancelled(job["id"], job["lease_token"]):
            raise Conflict("Lease lost before artifact publication")
        root = path.resolve()
        for file in sorted(root.rglob("*")):
            if file.is_file():
                if file.is_symlink() or not file.resolve().is_relative_to(root):
                    raise StoreError("Artifact directory contains an unsafe path")
                self.storage.put_file(job["artifact_prefix"] + "/" + file.relative_to(root).as_posix(), file)

    def job_context(self, job: dict) -> dict:
        with self.transaction(False) as conn:
            asset = one(conn, select(db.assets).where(db.assets.c.id == job["asset_id"], ~db.assets.c.deleted))
            run = one(conn, select(db.runs).where(db.runs.c.id == job["run_id"]))
            stages = all_rows(conn, select(db.jobs).where(db.jobs.c.run_id == job["run_id"], db.jobs.c.status == "complete", db.jobs.c.kind != "render"))
        results = {stage["kind"]: stage["result"] for stage in stages}
        base = self._materialize(run["id"], results) if results.get("prepare") else self.work_dir(job)
        return {"asset": asset, "run": run, "source": self.storage.local_path(asset["source_key"]), "base": base,
                **{key: results.get(key) for key in ("prepare", "asr", "ocr", "visual")}}

    def _materialize(self, run_id: str, manifest: dict) -> Path:
        # Immutable stage hashes choose a new cache directory when a dependency changes.
        signature = hashlib.sha256(canonical(manifest).encode()).hexdigest()[:20]
        base = self.config.data_dir / "materialized" / run_id / signature
        base.mkdir(parents=True, exist_ok=True)
        prepare = manifest["prepare"]
        names = [prepare.get("proxy"), prepare.get("audio"), *[frame["path"] for frame in prepare.get("frames", [])]]
        for name in filter(None, names):
            self._cache_object(prepare["artifact_prefix"] + "/" + name, base, name)
        visual = manifest.get("visual") or {}
        if visual.get("index"):
            self._cache_object(visual["artifact_prefix"] + "/" + visual["index"], base, visual["index"])
        return base

    def _cache_object(self, key: str, base: Path, name: str) -> None:
        destination = (base / name).resolve()
        if not destination.is_relative_to(base.resolve()) or Path(name).is_absolute():
            raise StoreError("Invalid artifact-relative path")
        if destination.exists():
            return
        source = self.storage.local_path(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + "." + uid() + ".tmp")
        try:
            try:
                os.link(source, temporary)
            except OSError:
                shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def complete_manifest(self, run_id: str) -> tuple[dict, Path]:
        with self.transaction(False) as conn:
            run = one(conn, select(db.runs).join(db.assets, db.assets.c.id == db.runs.c.asset_id).where(
                db.runs.c.id == run_id, ~db.assets.c.deleted))
            if run["status"] != "ready" or not run["manifest"]:
                raise Conflict("Video analysis is not ready yet")
        manifest = run["manifest"]
        return manifest, self._materialize(run_id, manifest)

    def worker_seen(self, worker_id: str, queue: str) -> None:
        with self.transaction() as conn:
            if conn.execute(select(db.workers.c.id).where(db.workers.c.id == worker_id)).scalar():
                conn.execute(update(db.workers).where(db.workers.c.id == worker_id).values(queue=queue, seen=time.time()))
            else:
                conn.execute(insert(db.workers).values(id=worker_id, queue=queue, seen=time.time()))

    def jobs(self, project_id: str) -> list[dict]:
        with self.transaction(False) as conn:
            fields = [db.jobs.c[key] for key in ("id", "asset_id", "run_id", "kind", "queue", "status", "attempts", "error", "created_at", "updated_at")]
            return all_rows(conn, select(*fields).join(db.assets, db.assets.c.id == db.jobs.c.asset_id).where(db.jobs.c.project_id == project_id, ~db.assets.c.deleted).order_by(db.jobs.c.created_at))

    @staticmethod
    def validate_clips(clips: list[dict], duration: float) -> None:
        if not 0 <= len(clips) <= 30:
            raise StoreError("A timeline must contain at most 30 clips")
        total, ids = 0.0, set()
        for clip in clips:
            start, end = clip.get("start"), clip.get("end")
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)):
                raise StoreError("Clip times must be finite numbers")
            if start < 0 or end > duration or end - start < 0.1:
                raise StoreError("Clip range is outside the source video or too short")
            if not isinstance(clip.get("id"), str) or not 1 <= len(clip["id"]) <= 100 or clip["id"] in ids:
                raise StoreError("Clip identifiers must be unique")
            if len(clip.get("title", "")) > 200 or len(clip.get("subtitle", "")) > 2000:
                raise StoreError("Clip text exceeds the limit")
            ids.add(clip["id"])
            total += end - start
        if total > 600:
            raise StoreError("Exports are limited to 10 minutes")

    def save_timeline(self, project_id: str, user_id: str, version: int, asset_id: str, run_id: str, clips: list[dict]) -> dict:
        from .retrieval import manifest_evidence_ids

        with self.transaction() as conn:
            self.role(conn, project_id, user_id, "editor")
            asset = one(conn, select(db.assets).where(db.assets.c.id == asset_id, db.assets.c.project_id == project_id, ~db.assets.c.deleted))
            run = one(conn, select(db.runs).where(db.runs.c.id == run_id, db.runs.c.asset_id == asset_id, db.runs.c.status == "ready"), "Ready analysis run not found")
            self.validate_clips(clips, run["manifest"]["prepare"]["duration"])
            allowed = manifest_evidence_ids(run["manifest"])
            for clip in clips:
                references = clip.get("evidence_ids", [])
                if (not isinstance(references, list) or len(references) > 30
                        or any(not isinstance(ref, str) or ref not in allowed for ref in references)):
                    raise StoreError("A clip references evidence absent from this analysis run")
            if asset["duration"] is None:
                raise Conflict("Asset is not ready")
            changed = conn.execute(update(db.projects).where(db.projects.c.id == project_id, db.projects.c.version == version).values(version=version + 1, updated_at=now())).rowcount
            if not changed:
                raise Conflict("A newer edit was saved. Reload or keep your local changes before retrying.")
            data = dict(project_id=project_id, version=version + 1, asset_id=asset_id, run_id=run_id, clips=clips, user_id=user_id, updated_at=now())
            conn.execute(insert(db.timelines).values(**data))
            return data

    def timeline_versions(self, project_id: str, user_id: str) -> list[dict]:
        with self.transaction(False) as conn:
            self.role(conn, project_id, user_id)
            return all_rows(conn, select(db.timelines).where(db.timelines.c.project_id == project_id).order_by(db.timelines.c.version.desc()))

    def restore_timeline(self, project_id: str, user_id: str, version: int, target_version: int) -> dict:
        with self.transaction(False) as conn:
            self.role(conn, project_id, user_id, "editor")
            target = self._timeline(conn, project_id, target_version)
        return self.save_timeline(project_id, user_id, version, target["asset_id"], target["run_id"], target["clips"])

    def create_export(self, project_id: str, user_id: str, version: int) -> dict:
        with self.transaction() as conn:
            self.role(conn, project_id, user_id, "editor")
            one(conn, select(db.projects).where(db.projects.c.id == project_id).with_for_update())
            timeline = self._timeline(conn, project_id, version)
            if not timeline["clips"]:
                raise StoreError("Save a timeline before exporting")
            one(conn, select(db.assets).where(db.assets.c.id == timeline["asset_id"], ~db.assets.c.deleted))
            existing = conn.execute(select(db.exports).where(db.exports.c.project_id == project_id, db.exports.c.version == version, db.exports.c.status.in_(["queued", "running", "ready"]))).mappings().first()
            if existing:
                return dict(existing)
            export_id = uid()
            payload = {"export_id": export_id, "asset_id": timeline["asset_id"], "run_id": timeline["run_id"], "clips": timeline["clips"], "version": version}
            job = self._job(conn, project_id, timeline["asset_id"], timeline["run_id"], "render", "cpu", [], payload)
            data = dict(id=export_id, project_id=project_id, user_id=user_id, version=version, asset_id=timeline["asset_id"], run_id=timeline["run_id"], clips=timeline["clips"], status="queued", job_id=job["id"], result=None, created_at=now())
            conn.execute(insert(db.exports).values(**data))
            return data

    def export_file(self, export_id: str, user_id: str, kind: str) -> Path:
        with self.transaction(False) as conn:
            data = one(conn, select(db.exports).join(db.assets, db.assets.c.id == db.exports.c.asset_id).where(db.exports.c.id == export_id, ~db.assets.c.deleted))
            self.role(conn, data["project_id"], user_id)
            if data["status"] != "ready":
                raise Conflict("Export is not ready")
            name = data["result"].get(kind)
            if not name:
                raise NotFound("Export artifact not found")
        return self.storage.local_path(data["result"]["artifact_prefix"] + "/" + name)
