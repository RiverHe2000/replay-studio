# Replay Studio implementation contract

This file defines shared interfaces for parallel development. All implementation lives in this repository. Python 3.12, FastAPI, SQLAlchemy 2 (Postgres deployment and SQLite local mode), React/TypeScript/Vite. JSON timestamps UTC ISO, media time in seconds on a normalized source playback timeline. IDs UUID hex strings. No source file paths in public responses.

## Ownership

- Root: `config.py`, `db.py`, `store.py`, `storage.py`, `api.py`, `cli.py`, packaging, backend security tests, integration/review.
- Media agent: `media.py`, `analysis.py`, `retrieval.py`, `evaluation.py`, corresponding tests.
- Workflow agent: `worker.py`, fixture/verification scripts, deployment files, workflow tests, operator docs.
- Frontend agent: all `frontend/` files.

Do not modify another owner's files without agreement. Use `src/replay_studio/` for Python modules.

## Python media interfaces

Functions raise clear exceptions; external subprocess calls use argument arrays, no shell, bounded timeouts. `cancel` is a callable returning bool and may be omitted.

`media.prepare(source: Path, out: Path, *, cancel=None) -> dict`: ffprobe, normalize to H264/AAC browser proxy, extract WAV and sampled JPEGs. Returns `{"duration":float,"width":int,"height":int,"has_audio":bool,"proxy":"proxy.mp4","audio":"audio.wav" or null,"frames":[{"time":float,"path":"frames/....jpg"}],"time_origin":float,"warnings":[str]}`. All artifact paths relative to out, safe names. `prepare` limits input to <=1800 seconds/1920x1080 output, preserves timeline semantics across nonzero audio starts and VFR. Must support no audio.

`media.render(source: Path, clips: list[dict], out: Path, *, cancel=None) -> dict`: normalized proxy source; clips `{id,start,end,title,subtitle}`. Validate finite bounded positive intervals and max total length 600s. Export `output.mp4`, `output.srt`, `sources.json`. Returns `{video,subtitles,provenance,duration}` relative paths. Render accurately with reencoding. Existing subtitle text is untrusted; do not interpolate into shell/filter code unsafely.

`analysis.transcribe(audio: Path|None, out: Path, *, cancel=None) -> dict`: `{segments:[{start,end,text}], backend:str, status:"complete"|"unavailable", warning:str|null}`. Default real faster-whisper small/base CPU int8 or configured CUDA. Environment `REPLAY_ASR_MODEL` default base, `REPLAY_MODEL_DEVICE` default cpu; downloading model allowed for real run; exceptions never converted into fake transcripts.

`analysis.ocr(frames: list[dict], base:Path, out:Path, *, cancel=None) -> dict`: `{segments:[{start,end,text,frame}], backend,status,warning}`. Default actual RapidOCR CPU; unavailable explicit.

`analysis.visual(frames:list[dict], base:Path, out:Path, *, cancel=None) -> dict`: create real CLIP visual embedding index if dependencies available, persistent safe npz. `{index:"visual.npz"|null,model:str,status,warning}`. Env `REPLAY_VISUAL_MODEL` default openai/clip-vit-base-patch32. Optional local VLM enrichment by `REPLAY_VLM_MODEL`; do not pretend embeddings are VLM.

`retrieval.search(query:str, manifest:dict, base:Path, *, mode:str="fusion", limit:int=8) -> list[dict]`: manifest contains prepare/asr/ocr/visual result dictionaries. Result `{id,start,end,score,text,modalities:["speech"|"screen"|"visual"],frame:str|null,evidence:[{modality,text,time}],uncertain:bool}`. Modes speech, speech_ocr, fusion. Hard negatives/abstention supported. No fake confidence percentages.

`retrieval.plan(query:str, hits:list[dict], duration:float, *, target_seconds:float=90) -> dict`: `{clips:[{id,start,end,title,subtitle,evidence_ids:[str]}],warnings:[str],strategy:str}`. Honest deterministic baseline planner; optional model planner only if truly implemented, output must be validated. End-to-end UI labels strategy. ASR/OCR outputs have separate provenance.

## Store/worker interfaces

`Config.from_env()` yields `data_dir:Path`, `database_url:str`, `lease_seconds:int` (default 60), `max_attempts:int` (3), plus auth/upload fields. `Store(config)` creates schema. `store.storage` is `Storage` adapter. `Storage.local_path(key)` gets local usable cached Path; `put_file(key,path)` persists; `exists(key)`, `delete_prefix(prefix)`. Keys only relative, no traversal.

Each asset input resides at `assets/{asset_id}/source`, each analysis run directory at `runs/{run_id}/`, renders at `exports/{export_id}/`. Local worker can use `store.work_dir(job)` returning a lease-specific local Path. `store.publish_dir(job,path)` persists directory to its `artifact_prefix` and is called only after work; completed DB references point at immutable attempt prefix. API serves only referenced committed files. Source `store.storage.local_path(asset['source_key'])`.

Store methods (dict return shapes):
- `create_analysis(project_id, asset_id, config:dict|None=None) -> run dict`: schedules DAG prepare(cpu) -> asr(gpu),ocr(cpu),visual(gpu) -> index(cpu). Configuration fingerprint included; active matching request idempotent. `config` settings passed by worker, use explicit profiles.
- `create_export(project_id,user_id,version:int) -> export dict`: freezes saved timeline and queues render(cpu), payload includes asset_id/run_id/clips; only one asset per timeline v1.
- `claim(queue:str,worker_id:str) -> job|None`: job `{id,project_id,asset_id,run_id,kind,queue,payload,lease_token,artifact_prefix,attempts}`. Resource locks ensure at most one gpu job across workers in local mode. Block on unmet deps, propagate failed/cancelled deps.
- `heartbeat(job_id,token)->bool`, `cancelled(job_id,token)->bool`, `finish(job_id,token,result:dict)->bool`, `fail(job_id,token,error:str)->bool`. Finish fences expired token and deleted/cancelled scope. transient failure retry to max attempts; downstream blocked explicitly.
- `job_context(job)->dict`: `{asset:dict,run:dict,prepare:dict|None,asr:dict|None,ocr:dict|None,visual:dict|None,base:Path,source:Path}`. `base` materializes prepare artifacts; analysis returned artifact relative paths resolved by manifest into committed directories. `index` finish assembles durable manifest, sets run ready; render finish sets export ready.
- `complete_manifest(run_id)->tuple[dict,Path]`: manifest and materialized base for search; paths for OCR frames/proxy relative to base, visual index path points to actual local materialization.
- `worker_seen(worker_id,queue)`, `jobs(project_id)` public safe status.

Worker CLI: `python -m replay_studio.cli worker --queue cpu|gpu|all --once|--drain`; `worker.run_worker(store,queue="all",once=False,drain=False)` and `worker.process_one(store,queue="all",worker_id="...")->bool` exposed. Heartbeat during long operations; return no false completion on lease loss. Each stage saves result JSON and metrics; external errors surfaced.

## HTTP API (same origin, cookie session + CSRF)

All routes `/api`. Errors `{detail:string}`. Cookies HttpOnly, session scoped; modifying authenticated requests require header `X-CSRF-Token` from `/auth/me`. Frontend fetch credentials same-origin. Auth response `{user:{id,email,name},csrf_token:string}`. Auth unauthenticated returns 401.

- GET `/health`: `{status,version}`; GET `/capabilities`: `{ffmpeg,asr,ocr,visual,vlm,storage,database,limits}` statuses, not fake readiness.
- GET `/auth/status`: `{needs_setup:bool,registration_open:bool}`.
- POST `/auth/register` `{email,password,name}`; POST `/auth/login` `{email,password}`; GET `/auth/me`; POST `/auth/logout`.
- GET `/projects` -> list `{id,name,role,created_at,updated_at}`; POST `/projects` `{name}` -> project.
- GET `/projects/{id}` -> `{id,name,role,assets:[asset],timeline:{version,asset_id,run_id,clips:[],updated_at},jobs:[],exports:[],members:[],comments:[]}`.
- POST `/projects/{id}/members` `{email,role:"editor"|"viewer"}` owner only; DELETE `/projects/{id}/members/{user_id}` owner only.
- POST `/projects/{id}/comments` `{text,time?:float,asset_id?:string}`; GET via project.
- POST `/projects/{id}/uploads` `{filename,size}` -> `{id,offset,chunk_size,max_size}`.
- GET `/uploads/{id}` -> `{id,offset,size,status}`. DELETE `/uploads/{id}` cancels an unfinished upload, releases quota and removes staging bytes; completed uploads require asset deletion.
- PUT `/uploads/{id}?offset=N` raw binary, `Content-Type:application/octet-stream`, max 4MiB chunks -> offset. Exact replay of already received chunk idempotent, conflicting overlap 409.
- POST `/uploads/{id}/complete` -> asset `{id,project_id,name,size,duration:null,status:"queued",run_id,created_at}`; automatically schedules analysis. Validation failure clean/retain resumable status explicitly.
- DELETE `/assets/{id}` -> 204. Owner/editor; invalidates pending tasks and access.
- POST `/assets/{id}/analyze` `{asr?:bool,ocr?:bool,visual?:bool}` -> run; defaults all true; disabled stages honestly marked skipped, index can use partial results.
- GET `/assets/{id}/media` streams committed proxy if ready, otherwise source (browser may not decode yet); supports Range via FileResponse.
- GET `/assets/{id}/frames/{frame_name}` committed frame JPEG only, no arbitrary nested path.
- GET `/assets/{id}/transcript` -> `{segments:[...],status,backend}`.
- POST `/assets/{id}/search` `{query,mode:"speech"|"speech_ocr"|"fusion",limit:8}` -> `{hits:[],mode,warnings:[]}`.
- POST `/assets/{id}/plan` `{query,mode,target_seconds}` -> `{clips,warnings,strategy,run_id,asset_id}` (does not overwrite saved timeline).
- PUT `/projects/{id}/timeline` `{version,asset_id,run_id,clips:[{id,start,end,title,subtitle,evidence_ids:[]}]}` -> updated timeline. Optimistic concurrency 409. Validate asset/run ownership, ready status, intervals, max10min total/max30 clips/length-limited texts; empty saved timelines are valid but not exportable; nonempty evidence IDs must exist in the selected run. Immutable previous versions.
- GET `/projects/{id}/timeline/versions` -> list versions; POST `/projects/{id}/timeline/restore` `{version,target_version}` -> new revision from old snapshot (CAS).
- POST `/projects/{id}/exports` `{version}` -> export; GET `/exports/{id}/video|subtitles|provenance` protected downloads.
- POST `/jobs/{id}/cancel` -> job; POST `/jobs/{id}/retry` -> job.

Project polling 2s while jobs active; no auto-overwrite of dirty editor. UI accessible labels, keyboard clips movement, errors, empty and loading states. All server assertions authoritative.

## Reliability and delivery

Use focused tests for invariants: auth/CSRF/isolation, upload replay, version conflict, fenced completion, recovery, render precision, invalid media, retrieval evidence. Deliver actual demo video and actual pipeline run, not mocked feature claims. Include deployment compose with Postgres and object storage, lock dependencies, CI, README, architecture/tradeoff records, measured results and review findings. External user study/cloud uptime pending unless actually observed.
