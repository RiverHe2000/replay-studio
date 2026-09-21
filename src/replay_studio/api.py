"""Same-origin web API. Every media/object access checks project membership."""

from __future__ import annotations

import hmac
import importlib.util
import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .config import Config
from .store import Forbidden, Store, StoreError

log = logging.getLogger(__name__)
COOKIE = "replay_session"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Register(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=256)
    name: str = Field(min_length=1, max_length=80)


class Login(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=120)


class MemberInput(Input):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["editor", "viewer"]


class CommentInput(Input):
    text: str = Field(min_length=1, max_length=2000)
    time: float | None = Field(default=None, ge=0, le=1800)
    asset_id: str | None = Field(default=None, max_length=32)


class UploadInput(Input):
    filename: str = Field(min_length=1, max_length=240)
    size: int = Field(gt=0)


class AnalyzeInput(Input):
    asr: bool = True
    ocr: bool = True
    visual: bool = True


class SearchInput(Input):
    query: str = Field(min_length=1, max_length=1000)
    mode: Literal["speech", "speech_ocr", "fusion"] = "fusion"
    limit: int = Field(default=8, ge=1, le=20)


class PlanInput(SearchInput):
    target_seconds: float = Field(default=90, ge=1, le=600)


class Clip(Input):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str = Field(default="", max_length=200)
    subtitle: str = Field(default="", max_length=2000)
    evidence_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(default_factory=list, max_length=30)


class TimelineInput(Input):
    version: int = Field(ge=0)
    asset_id: str = Field(min_length=1, max_length=32)
    run_id: str = Field(min_length=1, max_length=32)
    clips: list[Clip] = Field(default_factory=list, max_length=30)


class VersionInput(Input):
    version: int = Field(ge=1)


class RestoreInput(Input):
    version: int = Field(ge=0)
    target_version: int = Field(ge=1)


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()
    store = Store(config)

    @asynccontextmanager
    async def lifespan(app):
        yield
        store.engine.dispose()

    app = FastAPI(title="Replay Studio", version=__version__, lifespan=lifespan)
    app.state.store = store

    @app.exception_handler(StoreError)
    async def store_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            allowed = {str(request.base_url).rstrip("/"), *config.allow_origins}
            if origin and origin not in allowed:
                return JSONResponse({"detail": "Cross-origin writes are not allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "private, no-store"
        return response

    def actor(request: Request) -> str:
        identity = store.authenticate(request.cookies.get(COOKIE))
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not hmac.compare_digest(
            request.headers.get("x-csrf-token", ""), identity["csrf_token"],
        ):
            raise Forbidden("Session verification failed. Refresh and try again.")
        return identity["user"]["id"]

    def auth_response(identity: dict) -> JSONResponse:
        response = JSONResponse({"user": identity["user"], "csrf_token": identity["csrf_token"]})
        response.set_cookie(COOKIE, identity["token"], httponly=True, secure=config.secure_cookies,
                            samesite="lax", max_age=config.session_hours * 3600, path="/")
        return response

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/capabilities")
    def capabilities():
        def available(module):
            return importlib.util.find_spec(module) is not None
        return {"ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
                "asr": available("faster_whisper"), "ocr": available("rapidocr"),
                "visual": available("transformers") and available("torch"),
                "vlm": bool(os.getenv("REPLAY_VLM_MODEL")),
                "note": "Dependency/configuration availability; each completed run records actual model execution.",
                "storage": config.storage_backend, "database": "sqlite" if store.sqlite else "postgresql",
                "limits": {"max_upload_bytes": config.max_upload_bytes, "max_duration": 1800, "max_export_seconds": 600}}

    @app.get("/api/auth/status")
    def auth_status():
        return store.auth_status()

    @app.post("/api/auth/register")
    def register(body: Register, request: Request):
        store.auth_rate_limit("auth:" + (request.client.host if request.client else "unknown"))
        return auth_response(store.register(body.email, body.password, body.name))

    @app.post("/api/auth/login")
    def login(body: Login, request: Request):
        store.auth_rate_limit("auth:" + (request.client.host if request.client else "unknown"))
        return auth_response(store.login(body.email, body.password))

    @app.get("/api/auth/me")
    def me(request: Request):
        return store.authenticate(request.cookies.get(COOKIE))

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request, user_id: str = Depends(actor)):
        store.logout(request.cookies[COOKIE])
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/api/projects")
    def projects(user_id: str = Depends(actor)):
        return store.list_projects(user_id)

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectInput, user_id: str = Depends(actor)):
        return store.create_project(user_id, body.name)

    @app.get("/api/projects/{project_id}")
    def project(project_id: str, user_id: str = Depends(actor)):
        return store.project(project_id, user_id)

    @app.post("/api/projects/{project_id}/members", status_code=204)
    def add_member(project_id: str, body: MemberInput, user_id: str = Depends(actor)):
        store.add_member(project_id, user_id, body.email, body.role)
        return Response(status_code=204)

    @app.delete("/api/projects/{project_id}/members/{target_id}", status_code=204)
    def remove_member(project_id: str, target_id: str, user_id: str = Depends(actor)):
        store.remove_member(project_id, user_id, target_id)
        return Response(status_code=204)

    @app.post("/api/projects/{project_id}/comments", status_code=201)
    def comment(project_id: str, body: CommentInput, user_id: str = Depends(actor)):
        return store.comment(project_id, user_id, body.text, body.time, body.asset_id)

    @app.post("/api/projects/{project_id}/uploads", status_code=201)
    def upload_start(project_id: str, body: UploadInput, user_id: str = Depends(actor)):
        return store.init_upload(project_id, user_id, body.filename, body.size)

    @app.get("/api/uploads/{upload_id}")
    def upload_progress(upload_id: str, user_id: str = Depends(actor)):
        return store.upload(upload_id, user_id)

    @app.delete("/api/uploads/{upload_id}", status_code=204)
    def upload_cancel(upload_id: str, user_id: str = Depends(actor)):
        store.cancel_upload(upload_id, user_id)
        return Response(status_code=204)

    @app.put("/api/uploads/{upload_id}")
    async def upload_chunk(upload_id: str, request: Request, offset: int, user_id: str = Depends(actor)):
        # Bound the streaming body even when Content-Length is absent or dishonest.
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > config.chunk_size:
                raise HTTPException(413, "Upload chunk exceeds limit")
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(store.append_upload, upload_id, user_id, offset, bytes(content))

    @app.post("/api/uploads/{upload_id}/complete")
    def upload_complete(upload_id: str, user_id: str = Depends(actor)):
        return store.complete_upload(upload_id, user_id)

    @app.delete("/api/assets/{asset_id}", status_code=204)
    def asset_delete(asset_id: str, user_id: str = Depends(actor)):
        store.delete_asset(asset_id, user_id)
        return Response(status_code=204)

    @app.post("/api/assets/{asset_id}/analyze")
    def analyze(asset_id: str, body: AnalyzeInput, user_id: str = Depends(actor)):
        asset = store.asset(asset_id, user_id, "editor")
        return store.create_analysis(asset["project_id"], asset_id, body.model_dump())

    @app.get("/api/assets/{asset_id}/media")
    def media(asset_id: str, user_id: str = Depends(actor)):
        asset = store.asset(asset_id, user_id)
        if asset["status"] == "ready":
            manifest, base = store.complete_manifest(asset["run_id"])
            path = base / manifest["prepare"]["proxy"]
            return FileResponse(path, media_type="video/mp4", content_disposition_type="inline")
        return FileResponse(store.storage.local_path(asset["source_key"]), media_type="application/octet-stream", content_disposition_type="inline")

    @app.get("/api/assets/{asset_id}/frames/{frame_name}")
    def frame(asset_id: str, frame_name: str, user_id: str = Depends(actor)):
        asset = store.asset(asset_id, user_id)
        manifest, base = store.complete_manifest(asset["run_id"])
        matches = [f["path"] for f in manifest["prepare"].get("frames", []) if Path(f["path"]).name == frame_name]
        if not matches:
            raise HTTPException(404, "Frame not found")
        return FileResponse(base / matches[0], media_type="image/jpeg")

    @app.get("/api/assets/{asset_id}/transcript")
    def transcript(asset_id: str, user_id: str = Depends(actor)):
        asset = store.asset(asset_id, user_id)
        manifest, _ = store.complete_manifest(asset["run_id"])
        result = manifest["asr"]
        return {k: result.get(k) for k in ("segments", "status", "backend", "warning")}

    def get_hits(asset_id: str, body: SearchInput, user_id: str):
        from .retrieval import search
        if not body.query.strip():
            raise StoreError("Enter a search query")
        asset = store.asset(asset_id, user_id)
        manifest, base = store.complete_manifest(asset["run_id"])
        try:
            hits = search(body.query.strip(), manifest, base, mode=body.mode, limit=body.limit)
        except RuntimeError as exc:
            log.exception("Search backend could not complete")
            raise HTTPException(503, "Search backend is unavailable. Try speech or screen-text search.") from exc
        warnings = [r["warning"] for key in ("asr", "ocr", "visual") if (r := manifest.get(key)) and r.get("warning")]
        warnings = list(dict.fromkeys([*warnings, *[warning for hit in hits for warning in hit.get("warnings", [])]]))
        return asset, manifest, hits, warnings

    @app.post("/api/assets/{asset_id}/search")
    def search_media(asset_id: str, body: SearchInput, user_id: str = Depends(actor)):
        _, _, hits, warnings = get_hits(asset_id, body, user_id)
        return {"hits": hits, "mode": body.mode, "warnings": warnings}

    @app.post("/api/assets/{asset_id}/plan")
    def plan_media(asset_id: str, body: PlanInput, user_id: str = Depends(actor)):
        from .retrieval import plan
        asset, manifest, hits, warnings = get_hits(asset_id, body, user_id)
        result = plan(body.query, hits, manifest["prepare"]["duration"], target_seconds=body.target_seconds)
        result["warnings"] = [*result.get("warnings", []), *warnings]
        return {**result, "run_id": asset["run_id"], "asset_id": asset_id}

    @app.put("/api/projects/{project_id}/timeline")
    def save_timeline(project_id: str, body: TimelineInput, user_id: str = Depends(actor)):
        return store.save_timeline(project_id, user_id, body.version, body.asset_id, body.run_id, [clip.model_dump() for clip in body.clips])

    @app.get("/api/projects/{project_id}/timeline/versions")
    def versions(project_id: str, user_id: str = Depends(actor)):
        return store.timeline_versions(project_id, user_id)

    @app.post("/api/projects/{project_id}/timeline/restore")
    def restore(project_id: str, body: RestoreInput, user_id: str = Depends(actor)):
        return store.restore_timeline(project_id, user_id, body.version, body.target_version)

    @app.post("/api/projects/{project_id}/exports", status_code=201)
    def export(project_id: str, body: VersionInput, user_id: str = Depends(actor)):
        result = store.create_export(project_id, user_id, body.version)
        return {k: v for k, v in result.items() if k not in {"result", "clips"}}

    @app.get("/api/exports/{export_id}/{kind}")
    def download_export(export_id: str, kind: Literal["video", "subtitles", "provenance"], user_id: str = Depends(actor)):
        path = store.export_file(export_id, user_id, kind)
        suffix, mime = {"video": ("mp4", "video/mp4"), "subtitles": ("srt", "application/x-subrip"), "provenance": ("json", "application/json")}[kind]
        return FileResponse(path, media_type=mime, filename=f"replay-{export_id[:8]}.{suffix}")

    @app.post("/api/jobs/{job_id}/{action}")
    def job_action(job_id: str, action: Literal["cancel", "retry"], user_id: str = Depends(actor)):
        result = store.change_job(job_id, user_id, action)
        return {k: result[k] for k in ("id", "kind", "status", "attempts", "error")}

    frontend = Path(os.getenv("REPLAY_FRONTEND_DIR", str(Path(__file__).resolve().parents[2] / "frontend" / "dist"))).resolve()

    @app.get("/{path:path}")
    def frontend_route(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "API route not found")
        target = (frontend / path).resolve()
        if target.is_relative_to(frontend) and target.is_file():
            return FileResponse(target)
        if not (frontend / "index.html").is_file():
            return JSONResponse({"message": "Replay Studio API is ready. Build frontend/ to open the workbench.", "docs": "/docs"})
        return FileResponse(frontend / "index.html")

    return app
