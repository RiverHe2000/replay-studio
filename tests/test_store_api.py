"""Cross-boundary invariants: permissions, upload idempotency, fencing and edits."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from replay_studio import db
from replay_studio.api import create_app
from replay_studio.config import Config
from replay_studio.store import Conflict, Forbidden, NotFound, Store, StoreError, now, uid


@pytest.fixture
def store(tmp_path):
    result = Store(Config(tmp_path, registration_open=True, lease_seconds=3, max_attempts=2))
    yield result
    result.engine.dispose()


def user(store, email="one@example.test"):
    ident = uid()
    with store.transaction() as conn:
        conn.execute(insert(db.users).values(id=ident, email=email, name=email, password_hash="test-only", created_at=now()))
    return ident


def asset(store, owner, project=None):
    project = project or store.create_project(owner, "Test project")
    upload = store.init_upload(project["id"], owner, "example.mp4", 6)
    store.append_upload(upload["id"], owner, 0, b"abcdef")
    return project, store.complete_upload(upload["id"], owner)


def ready(store, source):
    while job := store.claim("all", "test-worker"):
        result = {"status": "complete", "segments": [], "warning": None}
        if job["kind"] == "prepare":
            result = {"duration": 30.0, "proxy": "proxy.mp4", "audio": None, "frames": []}
        if job["kind"] == "visual":
            result["index"] = None
        assert store.finish(job["id"], job["lease_token"], result)
    return source["run_id"]


def clips():
    return [{"id": "first", "start": 1.0, "end": 3.0, "title": "Example", "subtitle": "text", "evidence_ids": []}]


def test_chunk_replay_conflict_and_complete_idempotency(store):
    owner = user(store)
    project = store.create_project(owner, "P")
    upload = store.init_upload(project["id"], owner, "source.mp4", 6)
    assert store.append_upload(upload["id"], owner, 0, b"abc")["offset"] == 3
    assert store.append_upload(upload["id"], owner, 0, b"abc")["offset"] == 3
    with pytest.raises(Conflict):
        store.append_upload(upload["id"], owner, 0, b"xyz")
    with pytest.raises(Conflict):
        store.append_upload(upload["id"], owner, 4, b"e")
    with pytest.raises(Conflict):
        store.complete_upload(upload["id"], owner)
    store.append_upload(upload["id"], owner, 3, b"def")
    first = store.complete_upload(upload["id"], owner)
    assert store.complete_upload(upload["id"], owner) == first
    assert len(store.jobs(project["id"])) == 5


def test_crash_after_file_append_before_database_commit(store):
    owner = user(store)
    project = store.create_project(owner, "P")
    upload = store.init_upload(project["id"], owner, "source.mp4", 6)
    store.append_upload(upload["id"], owner, 0, b"abc")
    with store._upload_path(upload["id"]).open("ab") as stream:
        stream.write(b"WRONG-uncommitted")
    store.append_upload(upload["id"], owner, 3, b"def")
    assert store._upload_path(upload["id"]).read_bytes() == b"abcdef"


def test_pending_uploads_reserve_quota(tmp_path):
    store = Store(Config(tmp_path, max_upload_bytes=10, project_quota_bytes=10))
    owner = user(store)
    project = store.create_project(owner, "P")
    store.init_upload(project["id"], owner, "a.mp4", 6)
    with pytest.raises(StoreError, match="quota"):
        store.init_upload(project["id"], owner, "b.mp4", 6)
    store.engine.dispose()


def test_project_isolation_and_viewer_permissions(store):
    owner, outsider = user(store), user(store, "two@example.test")
    project, source = asset(store, owner)
    with pytest.raises(NotFound):
        store.asset(source["id"], outsider)
    store.add_member(project["id"], owner, "two@example.test", "viewer")
    assert store.asset(source["id"], outsider)["id"] == source["id"]
    with pytest.raises(Forbidden):
        store.delete_asset(source["id"], outsider)
    with pytest.raises(Forbidden):
        store.init_upload(project["id"], outsider, "x.mp4", 3)
    store.remove_member(project["id"], owner, outsider)
    with pytest.raises(NotFound):
        store.asset(source["id"], outsider)


def test_concurrent_claim_only_one_prepare_owner(store):
    owner = user(store)
    asset(store, owner)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda n: store.claim("all", f"w{n}"), range(4)))
    assert sum(job is not None for job in claims) == 1


def test_expired_token_cannot_commit_or_renew(store):
    owner = user(store)
    asset(store, owner)
    first = store.claim("cpu", "old")
    with store.transaction() as conn:
        conn.execute(update(db.jobs).where(db.jobs.c.id == first["id"]).values(lease_until=time.time() - 1))
    second = store.claim("cpu", "new")
    assert first["id"] == second["id"] and first["lease_token"] != second["lease_token"]
    assert not store.heartbeat(first["id"], first["lease_token"])
    assert not store.finish(first["id"], first["lease_token"], {"duration": 10})
    assert not store.fail(first["id"], first["lease_token"], "late error")
    assert store.finish(second["id"], second["lease_token"], {"duration": 10})


def test_gpu_resource_serializes_different_projects(store):
    owner = user(store)
    asset(store, owner)
    asset(store, owner)
    for _ in range(2):
        job = store.claim("cpu", "prepare")
        assert store.finish(job["id"], job["lease_token"], {"duration": 30})
    first = store.claim("gpu", "gpu-one")
    assert first is not None
    assert store.claim("gpu", "gpu-two") is None
    assert store.finish(first["id"], first["lease_token"], {"status": "complete"})
    assert store.claim("gpu", "gpu-two") is not None


def test_failures_block_descendants_and_retry_recovers(store):
    owner = user(store)
    project, source = asset(store, owner)
    for _ in range(2):
        job = store.claim("cpu", "broken")
        store.fail(job["id"], job["lease_token"], "bad media")
    assert store.claim("all", "idle") is None
    assert {j["status"] for j in store.jobs(project["id"])} == {"failed", "blocked"}
    store.change_job(job["id"], owner, "retry")
    assert store.claim("cpu", "retry")["attempts"] == 1
    assert store.asset(source["id"])["status"] == "processing"


def test_cancel_fences_active_job_and_all_descendants(store):
    owner = user(store)
    project, source = asset(store, owner)
    job = store.claim("cpu", "work")
    store.change_job(job["id"], owner, "cancel")
    assert store.cancelled(job["id"], job["lease_token"])
    assert not store.finish(job["id"], job["lease_token"], {"duration": 30})
    assert all(j["status"] == "cancelled" for j in store.jobs(project["id"]))
    assert store.asset(source["id"])["status"] == "cancelled"


def test_deleted_asset_cannot_resurrect_from_active_job(store):
    owner = user(store)
    _, source = asset(store, owner)
    job = store.claim("cpu", "work")
    store.delete_asset(source["id"], owner)
    assert not store.finish(job["id"], job["lease_token"], {"duration": 30})
    with pytest.raises(NotFound):
        store.asset(source["id"], owner)


def test_optimistic_edits_and_restore_create_immutable_versions(store):
    owner = user(store)
    project, source = asset(store, owner)
    run_id = ready(store, source)
    timeline = store.save_timeline(project["id"], owner, 0, source["id"], run_id, clips())
    assert timeline["version"] == 1
    with pytest.raises(Conflict):
        store.save_timeline(project["id"], owner, 0, source["id"], run_id, clips())
    changed = [{**clips()[0], "end": 5}]
    store.save_timeline(project["id"], owner, 1, source["id"], run_id, changed)
    restored = store.restore_timeline(project["id"], owner, 2, 1)
    assert restored["version"] == 3 and restored["clips"] == clips()
    history = store.timeline_versions(project["id"], owner)
    assert [v["version"] for v in history] == [3, 2, 1]
    assert history[1]["clips"] == changed


@pytest.mark.parametrize("start,end", [(-1, 2), (3, 2), (0, 31), (0, float("nan")), (0, float("inf")), (True, 2)])
def test_invalid_clip_times_rejected(store, start, end):
    with pytest.raises(StoreError):
        store.validate_clips([{**clips()[0], "start": start, "end": end}], 30)


def test_cross_project_run_cannot_be_attached(store):
    owner = user(store)
    project, source = asset(store, owner)
    _, other = asset(store, owner)
    ready(store, source)
    with pytest.raises(NotFound):
        store.save_timeline(project["id"], owner, 0, source["id"], other["run_id"], clips())


def test_export_freezes_saved_revision_and_is_idempotent(store):
    owner = user(store)
    project, source = asset(store, owner)
    run_id = ready(store, source)
    store.save_timeline(project["id"], owner, 0, source["id"], run_id, clips())
    first = store.create_export(project["id"], owner, 1)
    assert store.create_export(project["id"], owner, 1)["id"] == first["id"]
    store.save_timeline(project["id"], owner, 1, source["id"], run_id, [{**clips()[0], "end": 8}])
    job = store.claim("cpu", "renderer")
    assert job["payload"]["clips"] == clips()


@pytest.mark.parametrize("key", ["../escape", "/absolute", "a/../../escape", "a\\b", "a//b", "a/./b", "C:/secret"])
def test_storage_rejects_unsafe_keys(store, key):
    with pytest.raises(ValueError):
        store.storage.local_path(key)


def test_api_auth_csrf_origin_and_media_isolation(tmp_path):
    app = create_app(Config(tmp_path, registration_open=True))
    with TestClient(app) as client:
        assert client.get("/api/projects").status_code == 401
        reply = client.post("/api/auth/register", json={"email": "a@example.test", "password": "securepassword", "name": "A"})
        assert reply.status_code == 200
        assert "HttpOnly" in reply.headers["set-cookie"]
        csrf = {"X-CSRF-Token": reply.json()["csrf_token"]}
        assert client.post("/api/projects", json={"name": "P"}).status_code == 403
        assert client.post("/api/projects", json={"name": "P"}, headers={**csrf, "Origin": "https://evil.invalid"}).status_code == 403
        project = client.post("/api/projects", json={"name": "P"}, headers=csrf).json()
        upload = client.post(f"/api/projects/{project['id']}/uploads", json={"filename": "a.mp4", "size": 3}, headers=csrf).json()
        assert client.put(f"/api/uploads/{upload['id']}?offset=0", content=b"abc", headers=csrf).status_code == 200
        source = client.post(f"/api/uploads/{upload['id']}/complete", headers=csrf).json()
        assert client.get(f"/api/assets/{source['id']}/media", headers={"Range": "bytes=0-1"}).status_code == 206
        assert client.post("/api/auth/logout", headers=csrf).status_code == 204
        assert client.get(f"/api/assets/{source['id']}/media").status_code == 401
        other = client.post("/api/auth/register", json={"email": "b@example.test", "password": "securepassword", "name": "B"})
        assert other.status_code == 200
        assert client.get(f"/api/assets/{source['id']}/media").status_code == 404
        assert client.get(f"/api/projects/{project['id']}").status_code == 404
        assert client.get("/api/nonexistent").status_code == 404


def test_schema_version_is_checked(store):
    with store.transaction() as conn:
        conn.execute(update(db.schema_version).values(version=999))
    with pytest.raises(RuntimeError, match="schema version"):
        Store(store.config)


def test_empty_edit_can_be_saved_but_not_exported(store):
    owner = user(store)
    project, source = asset(store, owner)
    run_id = ready(store, source)
    saved = store.save_timeline(project["id"], owner, 0, source["id"], run_id, [])
    assert saved["clips"] == []
    with pytest.raises(StoreError, match="Save a timeline"):
        store.create_export(project["id"], owner, 1)


def test_reanalysis_reuses_only_unchanged_successful_stages(store):
    owner = user(store)
    project, source = asset(store, owner)
    ready(store, source)
    changed = store.create_analysis(project["id"], source["id"], {"visual": False})
    with store.transaction(False) as conn:
        rows = {r["kind"]: dict(r) for r in conn.execute(select(db.jobs).where(db.jobs.c.run_id == changed["id"])).mappings()}
    assert rows["prepare"]["result"]["cache_hit"]
    assert rows["asr"]["result"]["cache_hit"]
    assert rows["ocr"]["result"]["cache_hit"]
    assert rows["visual"]["status"] == "queued"
    assert rows["index"]["status"] == "queued"


def test_unavailable_vlm_does_not_poison_cache(store, monkeypatch):
    monkeypatch.setenv("REPLAY_VLM_MODEL", "example/test-vlm")
    owner = user(store)
    project, source = asset(store, owner)
    while job := store.claim("all", "test"):
        result = {"status": "complete", "segments": []}
        if job["kind"] == "prepare":
            result["duration"] = 30
        elif job["kind"] == "visual":
            result.update(index=None, vlm={"status": "unavailable"})
        store.finish(job["id"], job["lease_token"], result)
    next_run = store.create_analysis(project["id"], source["id"])
    assert next_run["id"] != source["run_id"]
    with store.transaction(False) as conn:
        visual = conn.execute(select(db.jobs.c.status).where(db.jobs.c.run_id == next_run["id"], db.jobs.c.kind == "visual")).scalar()
    assert visual == "queued"


def test_blocked_first_hundred_jobs_do_not_starve_ready_work(store):
    owner = user(store)
    project, source = asset(store, owner)
    with store.transaction() as conn:
        conn.execute(update(db.jobs).values(created_at="2099", updated_at="2099"))
        dependency = conn.execute(select(db.jobs.c.id).where(db.jobs.c.kind == "visual")).scalar()
        for _ in range(110):
            job = store._job(conn, project["id"], source["id"], source["run_id"], "index", "cpu", [dependency], {})
            conn.execute(update(db.jobs).where(db.jobs.c.id == job["id"]).values(created_at="2000", updated_at="2000"))
    claimed = store.claim("cpu", "ready-worker")
    assert claimed["kind"] == "prepare"


def test_auth_limits_shared_across_store_instances(store):
    from replay_studio.store import TooManyRequests
    other = Store(store.config)
    store.auth_rate_limit("client", limit=2)
    other.auth_rate_limit("client", limit=2)
    with pytest.raises(TooManyRequests):
        store.auth_rate_limit("client", limit=2)
    other.engine.dispose()


def test_comment_time_binds_to_source_asset(store):
    owner = user(store)
    project, source = asset(store, owner)
    ready(store, source)
    with pytest.raises(StoreError, match="identify"):
        store.comment(project["id"], owner, "Here", 10)
    note = store.comment(project["id"], owner, "Here", 10, source["id"])
    assert note["asset_id"] == source["id"]
    with pytest.raises(StoreError, match="outside"):
        store.comment(project["id"], owner, "Past end", 31, source["id"])


def test_cancel_upload_releases_quota_and_fences_late_chunks(tmp_path):
    store = Store(Config(tmp_path, project_quota_bytes=6))
    owner = user(store)
    project = store.create_project(owner, "Quota")
    upload = store.init_upload(project["id"], owner, "source.mp4", 6)
    store.append_upload(upload["id"], owner, 0, b"abc")
    with pytest.raises(StoreError, match="quota"):
        store.init_upload(project["id"], owner, "other.mp4", 6)
    store.cancel_upload(upload["id"], owner)
    store.cancel_upload(upload["id"], owner)
    assert not store._upload_path(upload["id"]).exists()
    with pytest.raises(Conflict):
        store.append_upload(upload["id"], owner, 3, b"def")
    with pytest.raises(Conflict):
        store.complete_upload(upload["id"], owner)
    assert store.init_upload(project["id"], owner, "other.mp4", 6)["size"] == 6
    store.engine.dispose()


def test_other_member_cannot_cancel_someones_upload(store):
    owner, editor = user(store), user(store, "editor@example.test")
    project = store.create_project(owner, "Shared")
    store.add_member(project["id"], owner, "editor@example.test", "editor")
    upload = store.init_upload(project["id"], owner, "file.mp4", 6)
    with pytest.raises(NotFound):
        store.cancel_upload(upload["id"], editor)


def test_migrate_legacy_comments_and_preserve_user_data(store):
    from sqlalchemy import inspect
    owner = user(store)
    project = store.create_project(owner, "Legacy project")
    with store.transaction() as conn:
        conn.exec_driver_sql("DROP TABLE comments")
        conn.exec_driver_sql("CREATE TABLE comments (id VARCHAR(32) PRIMARY KEY, project_id VARCHAR(32) NOT NULL REFERENCES projects(id), user_id VARCHAR(32) NOT NULL REFERENCES users(id), text TEXT NOT NULL, time FLOAT, created_at VARCHAR(40) NOT NULL)")
        conn.exec_driver_sql("INSERT INTO comments VALUES (?, ?, ?, ?, ?, ?)", (uid(), project["id"], owner, "Legacy note", 1.0, now()))
        conn.exec_driver_sql("DROP TABLE auth_limits")
        conn.execute(update(db.schema_version).values(version=1))
    upgraded = Store(store.config)
    with upgraded.transaction(False) as conn:
        assert conn.execute(select(db.schema_version.c.version)).scalar() == 3
        assert "asset_id" in {c["name"] for c in inspect(conn).get_columns("comments")}
        assert "auth_limits" in inspect(conn).get_table_names()
        assert conn.execute(select(db.comments.c.text)).scalar() == "Legacy note"
    assert upgraded.list_projects(owner)[0]["id"] == project["id"]
    upgraded.engine.dispose()


def test_timeline_rejects_invented_evidence_from_client(store):
    owner = user(store)
    project, source = asset(store, owner)
    run_id = ready(store, source)
    with pytest.raises(StoreError, match="evidence absent"):
        store.save_timeline(project["id"], owner, 0, source["id"], run_id,
                            [{**clips()[0], "evidence_ids": ["invented-reference"]}])
    assert store.project(project["id"], owner)["timeline"]["version"] == 0


def test_cancelled_gpu_release_ack_cannot_release_new_owner(store):
    owner = user(store)
    _, source = asset(store, owner)
    prepared = store.claim("cpu", "prepare")
    store.finish(prepared["id"], prepared["lease_token"], {"duration": 30})
    job = store.claim("gpu", "first")
    assert not store.abandon(job["id"], job["lease_token"])
    store.change_job(job["id"], owner, "cancel")
    assert store.abandon(job["id"], job["lease_token"])
    store.change_job(job["id"], owner, "retry")
    replacement = store.claim("gpu", "second")
    assert replacement and replacement["lease_token"] != job["lease_token"]
    assert not store.abandon(job["id"], job["lease_token"])
    assert store.heartbeat(replacement["id"], replacement["lease_token"])


def test_s3_materialization_uses_short_atomic_staging_and_cleans_failure(store):
    from pathlib import Path

    class S3:
        fail = False

        def download_file(self, bucket, key, filename):
            path = Path(filename)
            assert path.parent == store.storage.root / ".staging"
            path.write_bytes(b"downloaded")
            if self.fail:
                raise OSError("interrupted transfer")

    store.storage.s3 = S3()
    target = store.storage.local_path("runs/long-attempt-path/subdir/proxy.mp4")
    assert target.read_bytes() == b"downloaded"
    store.storage.s3.fail = True
    with pytest.raises(OSError, match="interrupted"):
        store.storage.local_path("runs/other/proxy.mp4")
    assert list((store.storage.root / ".staging").iterdir()) == []
