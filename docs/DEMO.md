# Inspect the workflow in two minutes

The artifacts below were produced by the working application. The input is a 30-second, visibly labelled synthetic debugging recording with generated narration. They demonstrate processing and editing behavior; they do not establish real-world retrieval quality or user productivity.

## 1. Find and inspect a moment

The recorded workspace searches for `DATABASE_URL` and displays screen-text evidence next to the source player. Open a result's evidence and use its timestamp to inspect the original moment before adding it to a cut. The interface labels OCR sampling limits and visual similarity explicitly.

![Source recording, evidence results and saved edit](../artifacts/browser/demo-ready.png)

Compare speech-only and screen-text routes when running locally. A separate developer probe searched for `PORT=8080`, which appears on screen but is absent from the narration. [Recorded probe](../artifacts/demo-reviewed/screen-only-probe.json). This selected-after-inspection example demonstrates the OCR path, not an unseen-test improvement.

## 2. Review the saved edit and actual export

The screenshot shows an explicitly saved single-source cut, editable in/out points and a clip caption. The export below belongs to the recorded saved version:

- [Play or download the actual ~20-second MP4](../artifacts/demo-reviewed/output.mp4).
- [Inspect its separate SRT captions](../artifacts/demo-reviewed/output.srt).
- [Inspect the source manifest](../artifacts/demo-reviewed/sources.json): original/proxy hashes, selected source interval, output interval, evidence IDs and timeline version.
- [Read the run receipt](../artifacts/demo-reviewed/verification.json).

The MP4 is the rendered sample clip, not a recording of the interface. Captions are a separate SRT and may contain user-reviewed wording. An export is bound to a saved version; unsaved edits require another explicit save and export.

## 3. Inspect an engineering failure case

When two edits target the same saved version, the workbench preserves the local draft and asks the reviewer to resolve the conflict. The [recorded conflict view](../artifacts/browser/version-conflict.png) and [browser report](../artifacts/browser/summary.json) document that path. The [results report](RESULTS.md) also links actual worker recovery and PostgreSQL/S3 restore checks.

The retrieval diagnostic has only three answerable queries on one synthetic video: fusion did not exceed speech Recall@1. Keep that limit alongside the working demo when discussing the project.

## Reproduce the sample locally

Complete the installation in the [English README](../README.md#run-from-a-fresh-checkout). Use a fresh demonstration workspace and let the script create its first account. Do not create an account manually in that workspace before seeding.

In the first PowerShell terminal, from the repository root:

```powershell
.\scripts\start_local.ps1 -DataDirectory data/portfolio-demo
```

Leave the server and worker running. In a second terminal at the same repository root:

```powershell
$env:REPLAY_DATA_DIR = (Resolve-Path data/portfolio-demo).Path
.\.venv\Scripts\python.exe scripts/generate_fixture.py --audio
.\.venv\Scripts\python.exe scripts/seed_demo.py --access-file data/portfolio-demo-access.json --out reports/private/portfolio-demo
```

`generate_fixture.py --audio` uses installed Windows TTS (or espeak on a supported setup). Seeding performs real HTTP upload, model processing and export, so it takes longer than viewing the committed sample and may download model weights. Do not add `--run-worker` while the launcher's worker is running.

When seeding reports success, open **http://127.0.0.1:8080** and use the private login details generated in `data/portfolio-demo-access.json`. That file and the new workspace are ignored by Git. The script's new reports are saved under ignored `reports/private/portfolio-demo/`, preserving the committed historical evidence.

For a workspace that already has a different first account, use the existing-account instructions in the [Chinese guide](../README.zh-CN.md) and [browser guide](../frontend/README.md); seeding does not bypass closed registration. Stop the server with Ctrl+C after the demonstration.
