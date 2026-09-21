"""Durable stage execution with renewable leases and fenced completion.

The database owns DAG scheduling. A worker only executes claimed work and publishes
an immutable attempt; a stale worker can never commit its attempt to a run.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import DBAPIError

if TYPE_CHECKING:
    from replay_studio.store import Store

LOG = logging.getLogger(__name__)
QUEUES = {"cpu", "gpu", "all"}
_ENV_LOCK = threading.RLock()


@contextmanager
def _model_environment(settings: dict[str, Any]):
    """Freeze each run's model identities, language and declared device.

    The media adapter currently uses environment configuration. Serialize that bridge
    within a process; different worker processes retain their own environments.
    """
    mapping = {
        "asr_model": "REPLAY_ASR_MODEL",
        "asr_revision": "REPLAY_ASR_REVISION",
        "visual_model": "REPLAY_VISUAL_MODEL",
        "vlm_model": "REPLAY_VLM_MODEL",
        "asr_language": "REPLAY_ASR_LANGUAGE",
        "model_device": "REPLAY_MODEL_DEVICE",
        "visual_revision": "REPLAY_VISUAL_REVISION",
        "vlm_revision": "REPLAY_VLM_REVISION",
    }
    with _ENV_LOCK:
        previous = {env: os.environ.get(env) for key, env in mapping.items() if key in settings}
        try:
            for key, env in mapping.items():
                if key in settings:
                    os.environ[env] = str(settings[key] or "")
            yield
        finally:
            for env, value in previous.items():
                if value is None:
                    os.environ.pop(env, None)
                else:
                    os.environ[env] = value


class WorkCancelled(RuntimeError):
    """The owner cancelled work or the worker no longer owns its lease."""


class LeaseGuard:
    """Renew independently of CPU/model work and communicate cancellation safely."""

    def __init__(self, store: Store, job: dict[str, Any]) -> None:
        self.store = store
        self.job = job
        self.stop = threading.Event()
        self.cancel = threading.Event()
        self.lost = False
        duration = float(getattr(store.config, "lease_seconds", 60))
        self.interval = max(0.05, min(5.0, duration / 3.0))
        self.thread = threading.Thread(target=self._renew, name=f"lease-{job['id']}", daemon=True)

    def _check(self) -> None:
        try:
            if self.store.cancelled(self.job["id"], self.job["lease_token"]):
                self.cancel.set()
                return
            if not self.store.heartbeat(self.job["id"], self.job["lease_token"]):
                self.lost = True
                self.cancel.set()
        except Exception:
            # A disconnected worker cannot assert that it still owns the work.
            LOG.exception("lease renewal failed", extra={"job_id": self.job["id"]})
            self.lost = True
            self.cancel.set()

    def _renew(self) -> None:
        while not self.stop.wait(self.interval):
            self._check()
            if self.cancel.is_set():
                return

    def __enter__(self) -> LeaseGuard:
        self._check()
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop.set()
        self.thread.join(timeout=max(1.0, self.interval + 0.5))

    def check(self) -> None:
        if self.cancel.is_set():
            raise WorkCancelled("lease lost" if self.lost else "job cancelled")


def _artifact(base: Path, value: str | None) -> Path | None:
    if value is None:
        return None
    candidate = (base / value).resolve()
    if not candidate.is_relative_to(base.resolve()):
        raise ValueError("artifact path is outside the committed stage")
    if not candidate.is_file():
        raise FileNotFoundError(f"required stage artifact is missing: {value}")
    return candidate


def _execute(store: Store, job: dict[str, Any], out: Path, cancel: Callable[[], bool]) -> dict[str, Any]:
    from replay_studio import analysis, media

    ctx = store.job_context(job)
    kind = job["kind"]
    settings = (ctx.get("run") or {}).get("config") or {}
    if kind in {"asr", "ocr", "visual"} and settings.get(kind, True) is False:
        result: dict[str, Any] = {
            "status": "skipped",
            "backend": "disabled",
            "warning": "Disabled for this analysis run.",
        }
        result["index" if kind == "visual" else "segments"] = None if kind == "visual" else []
        if kind == "visual":
            result["model"] = "disabled"
        return result
    if kind == "prepare":
        return media.prepare(Path(ctx["source"]), out, cancel=cancel)
    prepared = ctx.get("prepare")
    if kind in {"asr", "ocr", "visual"}:
        if not prepared:
            raise ValueError("prepare dependency has no committed result")
        base = Path(ctx["base"])
        with _model_environment(settings):
            if kind == "asr":
                return analysis.transcribe(_artifact(base, prepared.get("audio")), out, cancel=cancel)
            if kind == "ocr":
                return analysis.ocr(prepared.get("frames", []), base, out, cancel=cancel)
            return analysis.visual(prepared.get("frames", []), base, out, cancel=cancel)
    if kind == "index":
        if not prepared:
            raise ValueError("cannot index without a prepared source")
        stages = {name: (ctx.get(name) or {}).get("status", "missing") for name in ("asr", "ocr", "visual")}
        if "missing" in stages.values():
            raise ValueError("cannot index: a dependency has no committed result")
        return {
            "status": "complete",
            "stages": stages,
            "warnings": [
                (ctx[name] or {}).get("warning") for name in stages if (ctx[name] or {}).get("warning")
            ],
        }
    if kind == "render":
        payload = job.get("payload") or {}
        manifest, base = store.complete_manifest(payload.get("run_id") or job["run_id"])
        source = _artifact(Path(base), manifest["prepare"]["proxy"])
        assert source is not None
        result = media.render(source, payload["clips"], out, cancel=cancel)
        provenance_path = _artifact(out, result["provenance"])
        assert provenance_path is not None
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        if not isinstance(provenance, dict):
            raise ValueError("render provenance must be a JSON object")
        provenance.update(
            source_asset_id=job["asset_id"],
            analysis_run_id=payload.get("run_id") or job["run_id"],
            timeline_version=payload["version"],
            original_sha256=ctx["asset"]["sha256"],
            proxy_sha256=provenance.get("source_sha256"),
            source_sha256_kind="normalized_proxy",
            source_time_origin=manifest["prepare"]["time_origin"],
            analysis_fingerprint=ctx["run"]["fingerprint"],
        )
        provenance_path.write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
        )
        return result
    raise ValueError(f"unknown job kind: {kind}")


def process_one(store: Store, queue: str = "all", worker_id: str = "worker") -> bool:
    """Return whether work was claimed, including failed/cancelled attempts."""
    if queue not in QUEUES:
        raise ValueError("queue must be cpu, gpu or all")
    store.worker_seen(worker_id, queue)
    job = store.claim(queue, worker_id)
    if job is None:
        return False
    started = time.monotonic()
    token = job["lease_token"]
    guard: LeaseGuard | None = None
    try:
        with LeaseGuard(store, job) as guard:
            guard.check()
            out = Path(store.work_dir(job))
            out.mkdir(parents=True, exist_ok=True)
            result = _execute(store, job, out, guard.cancel.is_set)
            guard.check()
            if not isinstance(result, dict):
                raise TypeError("stage must return a result dictionary")
            metrics = {
                "job_id": job["id"],
                "kind": job["kind"],
                "attempt": job["attempts"],
                "elapsed_seconds": round(time.monotonic() - started, 6),
                "worker_id": worker_id,
            }
            (out / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
            )
            (out / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
            guard.check()
            store.publish_dir(job, out)
            guard.check()
            # The store performs the final ownership/cancellation check atomically.
            if not store.finish(job["id"], token, result):
                LOG.warning("completion fenced; attempt left unreferenced", extra={"job_id": job["id"]})
            else:
                LOG.info("stage committed", extra={"job_id": job["id"], **metrics})
    except WorkCancelled as exc:
        LOG.info("stage abandoned: %s", exc, extra={"job_id": job["id"]})
    except Exception as exc:
        if guard is not None and guard.cancel.is_set():
            LOG.info("stage stopped after cancellation or lease loss", extra={"job_id": job["id"]})
            return True
        LOG.exception("stage failed", extra={"job_id": job["id"]})
        # fail() is fenced too; a late failure cannot damage a newer attempt.
        store.fail(job["id"], token, f"{type(exc).__name__}: {exc}"[:2000])
    finally:
        # Only after execution stopped. Exact-token fencing prevents this worker
        # from releasing a replacement's resource, even after a long model call.
        try:
            store.abandon(job["id"], token)
        except DBAPIError:
            LOG.exception("resource release unavailable; lease will expire", extra={"job_id": job["id"]})
    return True


def run_worker(store: Store, queue: str = "all", once: bool = False, drain: bool = False) -> None:
    if queue not in QUEUES:
        raise ValueError("queue must be cpu, gpu or all")
    worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    reconnect_delay = 1.0
    while True:
        try:
            worked = process_one(store, queue=queue, worker_id=worker_id)
            reconnect_delay = 1.0
        except DBAPIError:
            if once or drain:
                raise
            LOG.exception("database unavailable; retrying in %.1f seconds", reconnect_delay)
            try:
                time.sleep(reconnect_delay)
            except KeyboardInterrupt:
                return
            reconnect_delay = min(reconnect_delay * 2, 30.0)
            continue
        except KeyboardInterrupt:
            LOG.info("worker interrupted; uncommitted lease will expire")
            return
        if once or (drain and not worked):
            return
        if not worked:
            time.sleep(1.0)
