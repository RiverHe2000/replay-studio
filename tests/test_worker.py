from __future__ import annotations

import importlib.util
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from replay_studio import worker


def test_run_model_identity_is_frozen_and_environment_restored(monkeypatch):
    import os

    monkeypatch.setenv("REPLAY_ASR_MODEL", "worker-default")
    monkeypatch.setenv("REPLAY_MODEL_DEVICE", "cpu")
    monkeypatch.delenv("REPLAY_VLM_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="model failed"):
        with worker._model_environment(
            {"asr_model": "run-pinned-model", "vlm_model": "", "model_device": "cuda", "asr_language": "en"}
        ):
            assert os.environ["REPLAY_ASR_MODEL"] == "run-pinned-model"
            assert os.environ["REPLAY_VLM_MODEL"] == ""
            assert os.environ["REPLAY_MODEL_DEVICE"] == "cuda"
            assert os.environ["REPLAY_ASR_LANGUAGE"] == "en"
            raise RuntimeError("model failed")
    assert os.environ["REPLAY_ASR_MODEL"] == "worker-default"
    assert os.environ["REPLAY_MODEL_DEVICE"] == "cpu"
    assert "REPLAY_VLM_MODEL" not in os.environ


class MemoryLeaseStore:
    def __init__(self, directory: Path):
        self.config = SimpleNamespace(lease_seconds=0.15)
        self.directory = directory
        self.job = {"id": "job-1", "kind": "prepare", "lease_token": "attempt-a", "attempts": 1}
        self.claimed = False
        self.heartbeats = 0
        self.live = True
        self.cancel_requested = False
        self.cancel_observed = threading.Event()
        self.calls: list[str] = []
        self.on_publish = None
        self.abandoned = []

    def worker_seen(self, *_):
        pass

    def claim(self, *_):
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    def cancelled(self, *_):
        if self.cancel_requested:
            self.cancel_observed.set()
        return self.cancel_requested

    def heartbeat(self, *_):
        self.heartbeats += 1
        return self.live

    def work_dir(self, *_):
        return self.directory / "attempt-a"

    def publish_dir(self, job, path):
        assert job["lease_token"] == "attempt-a"
        assert json.loads((path / "result.json").read_text())["duration"] == 3
        assert (path / "metrics.json").is_file()
        self.calls.append("publish")
        if self.on_publish:
            self.on_publish()

    def finish(self, *_):
        self.calls.append("finish")
        return self.live and not self.cancel_requested

    def fail(self, _id, token, error):
        assert token == "attempt-a"
        self.calls.append("fail:" + error)
        return self.live

    def abandon(self, job_id, token):
        self.abandoned.append((job_id, token))
        return not self.live or self.cancel_requested


def wait_until(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("timed out waiting for heartbeat")


def test_long_stage_renews_and_publishes_before_commit(tmp_path, monkeypatch):
    store = MemoryLeaseStore(tmp_path)

    def execute(_store, _job, out, cancel):
        wait_until(lambda: store.heartbeats >= 3)
        assert not cancel()
        (out / "proxy.mp4").write_bytes(b"artifact")
        return {"duration": 3}

    monkeypatch.setattr(worker, "_execute", execute)
    assert worker.process_one(store)
    assert store.calls == ["publish", "finish"]
    assert not worker.process_one(store)


def test_lost_lease_abandons_stage_without_publishing_or_failing_new_owner(tmp_path, monkeypatch):
    store = MemoryLeaseStore(tmp_path)

    def execute(_store, _job, _out, cancel):
        store.live = False
        wait_until(cancel)
        return {"duration": 3}

    monkeypatch.setattr(worker, "_execute", execute)
    assert worker.process_one(store)
    assert store.calls == []
    assert store.abandoned == [("job-1", "attempt-a")]


def test_cancel_while_uploading_does_not_commit_attempt(tmp_path, monkeypatch):
    store = MemoryLeaseStore(tmp_path)
    monkeypatch.setattr(worker, "_execute", lambda *_: {"duration": 3})

    def during_publish():
        store.cancel_requested = True
        assert store.cancel_observed.wait(timeout=2)

    store.on_publish = during_publish
    assert worker.process_one(store)
    assert store.calls == ["publish"]


def test_external_error_is_reported_with_same_lease(tmp_path, monkeypatch):
    store = MemoryLeaseStore(tmp_path)

    def execute(*_):
        raise RuntimeError("ffmpeg rejected invalid input")

    monkeypatch.setattr(worker, "_execute", execute)
    assert worker.process_one(store)
    assert store.calls == ["fail:RuntimeError: ffmpeg rejected invalid input"]


def test_artifact_must_stay_in_committed_directory(tmp_path):
    source = tmp_path / "base"
    source.mkdir()
    (tmp_path / "outside.wav").write_bytes(b"x")
    with pytest.raises(ValueError, match="outside"):
        worker._artifact(source, "../outside.wav")


def test_drain_stops_when_no_eligible_jobs_remain(tmp_path, monkeypatch):
    store = MemoryLeaseStore(tmp_path)
    monkeypatch.setattr(worker, "_execute", lambda *_: {"duration": 3})
    worker.run_worker(store, drain=True)
    assert store.calls == ["publish", "finish"]


def test_daemon_retries_database_disconnect_with_bounded_backoff(monkeypatch):
    attempts, sleeps = [], []

    def process(*_args, **_kwargs):
        attempts.append(1)
        if len(attempts) <= 7 or len(attempts) == 9:
            raise OperationalError("claim", {}, ConnectionError("temporarily offline"))
        if len(attempts) == 10:
            raise KeyboardInterrupt
        return True

    monkeypatch.setattr(worker, "process_one", process)
    monkeypatch.setattr(worker.time, "sleep", sleeps.append)
    worker.run_worker(object())
    assert sleeps == [1, 2, 4, 8, 16, 30, 30, 1]


@pytest.mark.parametrize("options", [{"once": True}, {"drain": True}])
def test_non_daemon_database_disconnect_fails_explicitly(monkeypatch, options):
    def process(*_args, **_kwargs):
        raise OperationalError("claim", {}, ConnectionError("temporarily offline"))

    monkeypatch.setattr(worker, "process_one", process)
    with pytest.raises(OperationalError):
        worker.run_worker(object(), **options)


def test_daemon_does_not_hide_programming_errors(monkeypatch):
    def process(*_args, **_kwargs):
        raise TypeError("programming bug")

    monkeypatch.setattr(worker, "process_one", process)
    with pytest.raises(TypeError, match="programming bug"):
        worker.run_worker(object())


def test_abandon_database_disconnect_does_not_mask_stage_result(tmp_path, monkeypatch):
    store = MemoryLeaseStore(tmp_path)
    monkeypatch.setattr(worker, "_execute", lambda *_: {"duration": 3})

    def abandon(*_):
        raise OperationalError("release", {}, ConnectionError("offline"))

    monkeypatch.setattr(store, "abandon", abandon)
    assert worker.process_one(store)
    assert store.calls == ["publish", "finish"]


@pytest.fixture
def backup_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "backup.py"
    spec = importlib.util.spec_from_file_location("replay_backup", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backup_restores_database_objects_and_upload_buffers(tmp_path, backup_module):
    import sqlite3

    from replay_studio.config import Config
    from replay_studio.storage import Storage

    original = Config(data_dir=tmp_path / "original")
    original.data_dir.mkdir()
    with sqlite3.connect(original.data_dir / "replay.sqlite3") as db:
        db.execute("CREATE TABLE sample (value TEXT)")
        db.execute("INSERT INTO sample VALUES ('source-record')")
    media = tmp_path / "source.mp4"
    media.write_bytes(b"immutable media bytes")
    Storage(original).put_file("assets/a/source", media)
    # A stage manifest filename must not be confused with the backup's root manifest.
    Storage(original).put_file("runs/r/manifest.json", media)
    uploads = original.data_dir / "uploads"
    uploads.mkdir()
    (uploads / "pending.part").write_bytes(b"partial upload")
    location = tmp_path / "snapshot"
    backup_module.backup(original, location)
    backup_module.verify(location)
    restored = Config(data_dir=tmp_path / "restored")
    backup_module.restore(restored, location)
    assert Storage(restored).local_path("assets/a/source").read_bytes() == media.read_bytes()
    assert (restored.data_dir / "uploads/pending.part").read_bytes() == b"partial upload"
    with sqlite3.connect(restored.data_dir / "replay.sqlite3") as db:
        assert db.execute("SELECT value FROM sample").fetchone()[0] == "source-record"
    with pytest.raises(ValueError, match="empty"):
        backup_module.restore(restored, location)


def test_corrupt_backup_is_rejected_before_restore(tmp_path, backup_module):
    directory = tmp_path / "snapshot"
    directory.mkdir()
    data = directory / "database.sqlite3"
    data.write_bytes(b"not-a-valid-snapshot")
    manifest = {
        "format": 1,
        "database": "sqlite",
        "files": [{"path": "database.sqlite3", "size": data.stat().st_size, "sha256": "wrong"}],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="integrity"):
        backup_module.verify(directory)


def test_render_provenance_uses_frozen_export_snapshot(tmp_path, monkeypatch):
    from replay_studio import media

    base = tmp_path / "committed"
    base.mkdir()
    (base / "proxy.mp4").write_bytes(b"proxy")
    out = tmp_path / "output"
    out.mkdir()
    frozen = [{"id": "clip", "start": 1, "end": 2, "title": "Snapshot", "subtitle": ""}]
    job = {
        "kind": "render",
        "asset_id": "asset-a",
        "run_id": "run-a",
        "payload": {"run_id": "run-a", "version": 7, "clips": frozen},
    }
    store = SimpleNamespace(
        job_context=lambda _: {
            "asset": {"sha256": "original-hash"},
            "run": {"fingerprint": "run-fingerprint", "config": {}},
        },
        complete_manifest=lambda _: ({"prepare": {"proxy": "proxy.mp4", "time_origin": 3.25}}, base),
        current_timeline={"version": 99, "clips": []},
    )

    def render(source, clips, target, **_):
        assert source == base / "proxy.mp4" and clips == frozen
        (target / "sources.json").write_text(json.dumps({"source_sha256": "proxy-hash", "clips": clips}))
        return {"provenance": "sources.json"}

    monkeypatch.setattr(media, "render", render)
    worker._execute(store, job, out, lambda: False)
    result = json.loads((out / "sources.json").read_text())
    assert result["timeline_version"] == 7
    assert result["source_asset_id"] == "asset-a" and result["analysis_run_id"] == "run-a"
    assert result["original_sha256"] == "original-hash" and result["proxy_sha256"] == "proxy-hash"
    assert result["source_sha256_kind"] == "normalized_proxy"
    assert result["source_time_origin"] == 3.25 and result["analysis_fingerprint"] == "run-fingerprint"


@pytest.fixture
def gc_module():
    script = Path(__file__).resolve().parents[1] / "scripts" / "garbage_collect.py"
    spec = importlib.util.spec_from_file_location("replay_garbage_collect", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def gc_store(tmp_path):
    from replay_studio.config import Config
    from replay_studio.store import Store

    store = Store(Config(data_dir=tmp_path / "installation", lease_seconds=2))
    user = store.register("gc@example.invalid", "not-a-real-password", "Garbage collection test")["user"][
        "id"
    ]
    project = store.create_project(user, "GC test")

    def upload(name):
        item = store.init_upload(project["id"], user, name, 5)
        store.append_upload(item["id"], user, 0, b"bytes")
        return store.complete_upload(item["id"], user)

    deleted, live = upload("deleted.mp4"), upload("live.mp4")
    store.delete_asset(deleted["id"], user)
    return store, deleted, live


def test_gc_dry_run_then_apply_preserves_live_asset_and_audit(gc_store, gc_module):
    from sqlalchemy import select

    from replay_studio import db

    store, deleted, live = gc_store
    source_key = f"assets/{deleted['id']}/source"
    with store.transaction(False) as connection:
        upload_id = connection.execute(
            select(db.uploads.c.id).where(db.uploads.c.asset_id == deleted["id"])
        ).scalar_one()
    upload_buffer = store.config.data_dir / "uploads" / f"{upload_id}.part"
    upload_buffer.write_bytes(b"leftover buffer")
    cache = store.config.data_dir / "materialized" / deleted["run_id"]
    cache.mkdir(parents=True)
    (cache / "cached.bin").write_bytes(b"cache")
    preview = gc_module.collect(store.config)
    assert preview["mode"] == "dry-run" and store.storage.exists(source_key) and cache.exists()
    with pytest.raises(ValueError, match="confirm-quiesced"):
        gc_module.collect(store.config, apply=True)
    applied = gc_module.collect(store.config, apply=True, confirm_quiesced=True)
    assert applied["status"] == "collected"
    assert not store.storage.exists(source_key) and not cache.exists()
    assert not upload_buffer.exists()
    assert store.storage.exists(f"assets/{live['id']}/source")
    with store.transaction(False) as connection:
        assert connection.execute(
            select(db.assets.c.deleted).where(db.assets.c.id == deleted["id"])
        ).scalar_one()
        assert connection.execute(select(db.jobs.c.id).where(db.jobs.c.asset_id == deleted["id"])).first()


def test_gc_refuses_recent_worker_and_unsafe_paths(gc_store, gc_module):
    store, _, _ = gc_store
    store.worker_seen("still-running", "cpu")
    with pytest.raises(ValueError, match="active jobs"):
        gc_module.collect(store.config, apply=True, confirm_quiesced=True)
    with pytest.raises(ValueError, match="outside"):
        gc_module.safe_target(store.config.data_dir, "../outside")


def test_gc_refuses_artifact_referenced_by_live_asset(gc_store, gc_module):
    from sqlalchemy import update

    from replay_studio import db

    store, deleted, live = gc_store
    with store.transaction() as connection:
        connection.execute(
            update(db.jobs)
            .where(db.jobs.c.asset_id == live["id"])
            .values(result={"artifact_prefix": f"runs/{deleted['run_id']}/attempts/shared"})
        )
    with pytest.raises(ValueError, match="live asset"):
        gc_module.collect(store.config, apply=True, confirm_quiesced=True)
    assert store.storage.exists(f"assets/{deleted['id']}/source")
