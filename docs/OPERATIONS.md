# Operating Replay Studio

The database is authoritative for DAG dependencies, attempts, leases, permissions,
timeline revisions and export snapshots. Immutable objects hold source videos,
normalized proxies, model artifacts and exports. Local caches are disposable;
database records without their referenced objects are not a recoverable backup.

## Portable deployment

Copy `deploy/env.example` to repository `.env`, replace both passwords with separate
long URL-safe random strings, then run:

```sh
docker compose up -d --build
docker compose logs -f api cpu-worker accelerator-worker
```

Open http://127.0.0.1:8080. The first account initializes the installation; subsequent
registration is closed unless `REPLAY_REGISTRATION_OPEN=true`. The application is
bound to loopback. A real external deployment needs a TLS reverse proxy, correct
origin configuration, and `REPLAY_SECURE_COOKIES=true`; the loopback example is not
evidence of cloud uptime or an Internet production deployment.

The stack starts PostgreSQL 16, MinIO, a one-time private bucket initializer, a
one-time schema initializer, API, CPU worker and an accelerator-queue worker. The
portable accelerator worker uses CPU inference. Queue names describe scheduling
resources; they do not imply that a CUDA device exists. Model weights download on
first use and persist in `model-cache`; cold-start model downloading takes time.
`/api/capabilities`, job errors and model stage warnings describe actual availability.
Unsupported or failed models are never replaced with fabricated transcripts.

Container locks target Linux x86-64. The CPU image installs hash-locked
`requirements-cpu.lock` with the official PyTorch CPU index; the CUDA image uses
`requirements-cuda.lock` and the cu128 index. This avoids silently installing CUDA
13 wheels into a CUDA 12.8 runtime. Docker Compose selects `linux/amd64` explicitly.
The frontend build uses pnpm 11.19.0 and its committed frozen lock/workspace files.
CPU/CUDA images install OpenCV's GL and GLib system libraries as well as FFmpeg.

MinIO's console is at http://127.0.0.1:9001. The example shares an administrative
object-store credential between services for local reproducibility. Before external
deployment replace it with an application-scoped S3 credential and policy allowing
only this bucket. Neither Postgres nor the S3 API is published to host interfaces.

`REPLAY_ASR_MODEL=base` and `REPLAY_VISUAL_MODEL=openai/clip-vit-base-patch32` are
defaults. Versions are model identifiers, not guarantees that weights are cached.
Use the same model configuration on equivalent workers and record it with results.
`REPLAY_VLM_MODEL=HuggingFaceTB/SmolVLM-256M-Instruct` enables optional frame
descriptions; the empty default disables them explicitly. Model descriptions remain
unverified evidence. Online planning/query encoding uses CPU; scheduled model stages
use the run's saved device setting.

## Optional CUDA worker

Install a compatible NVIDIA driver and container toolkit on the host. Start only
the selected accelerator service to avoid CPU and CUDA workers competing for jobs:

Set `REPLAY_MODEL_DEVICE=cuda` in repository `.env` first. This setting is shared by
the API and workers so a run's recorded execution profile agrees with inference.
Workers freeze the submitted model, language and device settings for each run;
changing worker defaults does not silently change an already queued job.

```sh
docker compose --profile cuda up -d --build postgres minio bucket bootstrap api cpu-worker gpu-worker
```

If the portable worker is already running, stop `accelerator-worker` first. The
CUDA image uses CUDA 12.8/cuDNN runtime; actual driver, Torch and CTranslate2
compatibility must be checked on the deployment host. A successful CPU run does
not verify CUDA. Start with one accelerator worker; database resource leases
serialize accelerator claims. Adding GPUs requires explicit resource scheduling,
not merely increasing the process count.

## Local development and actual integration verification

After installing project dependencies and FFmpeg, run API and worker in separate
terminals with the same `REPLAY_DATA_DIR`, database and storage settings:

```sh
python -m replay_studio.cli serve --port 8080
python -m replay_studio.cli worker --queue all
```

Create the reproducible sample and exercise a running API:

```sh
python scripts/generate_fixture.py --audio
python scripts/verify_e2e.py --base-url http://127.0.0.1:8080
```

Set `REPLAY_VERIFY_EMAIL` and `REPLAY_VERIFY_PASSWORD` in the environment for an
existing account. On an empty installation the script can create its first account.
This creates a new verification project and leaves it available for inspection.
Add `--run-worker` when no separate worker is running and the API shares the local
environment. Add `--require-modalities asr,ocr,visual` to require all real modalities
when using that local worker. The default local requirement is ASR plus OCR.

The generated video is visibly marked **SCRIPTED TECHNICAL FIXTURE** on every frame.
It shows an error, a configuration edit and successful checks. These are generated
screen images and optional operating-system TTS narration; the displayed test
count is fixture content, not an application test result. `ground_truth.json`
contains source intervals and out-of-scope queries. The script never inserts those
labels into the analyzer. Without an installed TTS engine the video contains
silence, and the manifest says so.

The verification script uses the actual HTTP API, exact chunk replay and conflicting
upload detection, a real worker DAG, all retrieval modes, deterministic evidence
planning, optimistic version conflict, FFmpeg export, SRT, source provenance, and
an `ffprobe` duration check. It writes `verification.json`, `output.mp4`,
`output.srt`, and `sources.json` in `artifacts/verification/`. A model-unavailable
result and a failing check remain visible; neither becomes a fake pass. The fixture
is a development integration test and does not measure accuracy on unseen users.

## Recovery and cancellation

Each claim receives a lease token and a distinct work directory. A background
heartbeat renews the token while FFmpeg/model work runs. If renewal fails or a user
cancels, cancellation is signalled to the stage; no completion is committed by a
worker that lost ownership. Model-native calls may finish their current batch before
observing cancellation. The final database transaction checks ownership and expiry
even if cancellation races with uploading artifacts.
After stage execution actually stops, `abandon(job_id, token)` releases only that
expired/cancelled token's resource lease. Fencing prevents stale database commits;
it does not forcibly free device memory. A hung native model call can require
stopping its process before a replacement safely uses the same physical GPU.

Long-running workers retry SQLAlchemy database connection failures with exponential
backoff from one to 30 seconds, resetting after a successful polling cycle. Errors
remain logged. `--once` and `--drain` fail explicitly on database disconnection for
automation; programming errors are not silently retried as connection problems.

The worker publishes artifacts under an immutable attempt prefix and only then
commits database references. A crash during publication may leave unreferenced
objects, but the UI cannot serve them through committed media endpoints. After the
lease expires another worker can claim the job with a new token and directory.
The default is three attempts; exhausted stages explicitly block their dependants.
Prepare, ASR, OCR and visual results are independently durable. Indexing waits for
the three analysis stages, including honest unavailable/skipped results.

Run `python scripts/verify_recovery.py` to execute a real process crash experiment
in a new isolated data directory. It kills an actual worker after the prepare job
is claimed, waits for lease expiry, starts a replacement, attempts a stale-token
commit while the replacement is running, and verifies the replacement's actual
FFmpeg proxy with `ffprobe`. Logs and `recovery.json` are preserved under
`artifacts/recovery/`. It does not alter an existing installation.

To demonstrate crash recovery on a dedicated installation: start processing a long
video, note the running job and attempt number, stop that worker process, wait for
the configured lease interval, restart a worker, and verify a higher attempt number
and one committed result. Keep the source and DB intact. Store and worker tests
cover stale completion, lease renewal and cancellation; a failure-injection demo
should additionally preserve its observed log and report.

## Back up and restore

The provided backup is a **quiesced snapshot**, not an online point-in-time backup.
Stop every writer: API, CPU worker, portable accelerator worker, CUDA worker and
any host-side worker. Do not remove volumes. Leave Postgres and MinIO running.

```sh
docker compose stop api cpu-worker accelerator-worker gpu-worker
docker compose run --rm --no-deps -v /absolute/backup-directory:/backup api \
  python scripts/backup.py backup /backup/snapshot-001 --confirm-quiesced
docker compose start api cpu-worker accelerator-worker
```

The bind directory must be writable by container UID 10001. This operation copies
the database (`pg_dump` custom format or SQLite's backup API), every current S3/local
object, and in-progress upload buffers. It includes a manifest with file sizes and
SHA-256 hashes. Backups contain private source media and account data: store them
under appropriately restricted access and encrypt them with your backup system.
No source-store credentials are saved in the manifest.

Verify and restore into a **new empty** target database and object bucket/directory:

```sh
python scripts/backup.py verify /absolute/backup-directory/snapshot-001
python scripts/backup.py restore /absolute/backup-directory/snapshot-001 --confirm-quiesced
```

Set `REPLAY_*` variables to the new target before restore. The restore validates every
file before copying, refuses nonempty targets, copies objects before the database,
and restores upload buffers. An interrupted restore requires a fresh empty target;
there is no overwrite switch. SQLite backups restore to SQLite, Postgres backups to
Postgres. Postgres tools must be compatible with the server major version; the
portable image includes PostgreSQL 16 client tools. Restart the API only after
restore succeeds; then log in, play a source, download an export and verify its
provenance. Expired in-flight jobs resume through normal lease recovery.

S3 version history, bucket IAM policies, external secrets and model-cache files are
not copied. Preserve deployment configuration and policies separately. Model weights
may be downloaded again; source media cannot. The script does not claim cross-host
disaster recovery until the restored installation has been verified on that host.

For the local SQLite profile, `python scripts/verify_backup.py --confirm-quiesced`
performs a backup into a new directory, restores to a new local installation, compares
all table row counts, and verifies each restored object against the snapshot's
SHA-256 manifest. Its report explicitly states that PostgreSQL/S3 was not tested.
It also reconstructs a ready manifest, retrieves actual transcript/OCR evidence and
encodes a new three-second MP4 from the restored proxy. It needs a ready analyzed
asset with text evidence; an empty installation cannot demonstrate that behavior.
Run this against a stopped dedicated test installation, with `REPLAY_DATA_DIR`
pointing to that installation. Existing snapshot/restore directories are refused.

For either storage profile, after a manual restore run:

```sh
python scripts/verify_restore.py /absolute/backup-directory/snapshot-001 --out artifacts/restore-check
```

This validates restored object hashes (S3 uses an independent empty cache), parses
the committed manifest, runs speech/OCR retrieval, probes source and existing
exports, and performs a real FFmpeg re-export. Set `REPLAY_VERIFY_EMAIL` and
`REPLAY_VERIFY_PASSWORD` to test restored login, a media range request and export
download through the API too. Credentials must belong to the selected restored
asset's project. Optional `REPLAY_VERIFY_SOURCE_DATABASE_URL` additionally compares
all source and target table counts before the login test creates a new session.
The generated probe export does not change the saved timeline or run jobs.

The checked-in `artifacts/pg-s3-backup/verification.json` records an actual local
PostgreSQL/S3 restore drill, not merely the SQLite test. This is single-host recovery
evidence; no cross-host disaster recovery, Docker startup or CUDA inference claim is
implied by it.

## Isolated Windows service verification

When Docker is unavailable, the same Store and worker can be verified against
unregistered, loopback-only server processes. The recorded test used PostgreSQL
16.15 Windows binaries from the [official PostgreSQL Windows download page](https://www.postgresql.org/download/windows/)
and its [EDB binary download provider](https://www.enterprisedb.com/download-postgresql-binaries),
plus MinIO [RELEASE.2025-09-07T16-13-09Z](https://github.com/minio/minio/releases/tag/RELEASE.2025-09-07T16-13-09Z).
The MinIO Windows executable's SHA-256 was checked against that release's published
checksum. PostgreSQL binaries were extracted to `.tools/postgresql16`; MinIO to
`.tools/minio.exe`. Neither binary nor generated credentials belong in Git.

The test initialized a dedicated `data/portable-services/pgdata` cluster with
SCRAM password authentication, started it with `pg_ctl` on `127.0.0.1:55433`, and
started MinIO against its own data directory on `127.0.0.1:59000` (console 59001).
No system service, existing database, Docker socket or system PATH was changed.
Point the standard `REPLAY_DATABASE_URL`, `REPLAY_STORAGE=s3`, `REPLAY_S3_*` variables
at an isolated database and bucket, use a short writable `REPLAY_DATA_DIR`, then run:

```sh
python scripts/verify_services.py --video data/fixtures/debugging/scripted_debugging.mp4
```

Use a dedicated empty test installation with registration enabled, a three-second
lease and at least five attempts for this fault-injection probe. It runs eight
concurrent CPU claimers, expires and fences an old token, executes a real prepare
stage, checks exclusive accelerator claims, and reads an S3 source through a separate
cache. It then logically deletes its probe asset. It does not measure inference
quality or actual GPU memory use. Reports are under `artifacts/services/`; a separate
fresh pipeline-v2 ASR/OCR/CLIP HTTP run is under `artifacts/verification-pg-s3-v2/`.
Stop only the dedicated MinIO process and use `pg_ctl -D <dedicated-cluster> stop`
after testing. Keep data directories and reports for inspection; do not reset Docker.

## Monitoring and storage maintenance

Inspect `/api/health` for API liveness, `/api/capabilities` for installed tooling,
project job statuses for failures/blocked stages, and container logs for stage
completion/errors. Each committed attempt has `result.json` and `metrics.json`
(duration, attempt number and worker identifier). These are operational evidence,
not a claim of an installed Prometheus/SLO monitoring stack.

Watch disk space for MinIO, upload buffers, per-worker caches, work directories and
model downloads. Local materialized caches can be rebuilt, but do not remove a work
directory owned by an active lease. Automatic age-based object garbage collection is
not enabled. The provided explicit collector removes objects for assets already
logically deleted by the application, while preserving database audit records:

```sh
python scripts/garbage_collect.py --report artifacts/gc-plan.json
python scripts/garbage_collect.py --confirm-quiesced --apply --report artifacts/gc-applied.json
```

The first command is always a dry run. Before applying, stop the API and **every**
worker and wait at least one configured lease interval after the last heartbeat.
The collector refuses active job/resource leases or recently seen workers; the
operator's quiescence assertion is still required because this tool cannot prove
that an external process has stopped. It protects references from live assets,
validates every local target inside `REPLAY_DATA_DIR`, and rejects symlink/junction
traversal. It deletes the selected asset/run/export object prefixes plus associated
upload buffers, materialized caches and job work directories, retaining all database
rows. Dry runs and repeated applications are safe; physical collection is irreversible
without a backup. Back up first if the deleted media may need recovery.

Compose uses separate API/worker cache volumes. Run collection with each service's
data volume mounted after stopping all writers; S3 prefix removal is idempotent,
but clearing one local volume cannot clear another worker's cache. Collection of
unreferenced attempts for **live** assets remains unimplemented; do not prune those
by age or delete broad `runs/` prefixes.
