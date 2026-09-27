# Controlled browser recording pilot v1

This is an AI-executed, silent screen-only pilot, not a human user study. Two actual Chromium viewport recordings exercise a local application with a scripted backend and seeded synthetic meeting content. They are software recordings rather than generated presentation slides. No private desktop or real client data is captured.

The [frozen protocol](../artifacts/controlled-pilot-v1/protocol.json) declares the workflows, eight answerable queries, two absent-feature queries, annotation rules, metrics and unchanged retrieval configuration before recording or model evaluation. Both recordings are from one application/session source group. They cannot establish independent-source generalization, speech quality, general multimodal superiority, or user time savings.

Full acceptable visibility windows are labelled from application state logs and independently reviewed video frames before OCR/CLIP evaluation. Labels are AI-authored references, not human/expert gold. Long visible states are not shortened to make IoU pass. The standard interval metrics remain primary; a separately named evidence-time containment diagnostic distinguishes finding a useful moment from selecting a good clip boundary. A silent source necessarily leaves the speech baseline empty.

The original synthetic integration fixture and its reports remain unchanged. This small pilot does not replace the larger, currently uncollected dataset or participant study in [EVALUATION.md](EVALUATION.md).

The protocol was committed in `583fc93`, then recordings and reviewed references in `3abe624`, before model evaluation. [Capture provenance](../artifacts/controlled-pilot-v1/PROVENANCE.md) documents the clock alignment, AI review, browser version deviation and byte-preservation correction. No query, threshold, video or reference interval was selected using retrieval output.

## Source evidence

| Recording | Actual browser actions | Retained source |
|---|---|---|
| Correction and approval | Paste transcript, select strategy, draft, inspect unsupported decision, edit, save and approve | [69.84-second silent WebM](../artifacts/controlled-pilot-v1/correction-approval/source.webm) · [references](../artifacts/controlled-pilot-v1/correction-approval/queries.json) · [frame review](../artifacts/controlled-pilot-v1/correction-approval/review-sheet-2.jpg) |
| Evidence and feedback | Different transcript, select strategy, draft, follow evidence chip, enter needs-work feedback, submit | [69.44-second silent WebM](../artifacts/controlled-pilot-v1/evidence-feedback/source.webm) · [references](../artifacts/controlled-pilot-v1/evidence-feedback/queries.json) · [frame review](../artifacts/controlled-pilot-v1/evidence-feedback/review-sheet-2.jpg) |

The source WebMs total about 12.3 MiB. Event logs, source/annotation receipts, contact sheets and clock-reading evidence are retained alongside them. The videos show only the disposable application viewport; the desktop, account sessions and other applications are not captured. Text is synthetic and the visible `example.invalid` approver is an instrumentation identity. A small persistent badge identifies the AI-controlled synthetic-content recording and its clock. This badge is present in the model inputs too; it is not a hidden source of reference labels.

## Reproduce the measured pipeline

Use a compatible Windows environment, FFmpeg on PATH, and the cached RapidOCR and `openai/clip-vit-base-patch32` weights. The runner forces CPU and offline Hugging Face resolution; it stops if a required model cannot run. It does not download a replacement model or invent observations. This observation used the existing development environment with Torch `2.11.0+cu128` forced to CPU, RapidOCR `3.9.2`, ONNX Runtime `1.29.0`, Transformers `5.16.1` and NumPy `2.2.6`; it is not another clean hash-locked CPU-wheel installation validation. Exact versions, model revision and OCR weight hashes are retained with the results.

The measurement runner calls Replay's actual media preparation, OCR, CLIP and retrieval modules directly. This pilot evaluates the retrieval pipeline over real software footage; the earlier HTTP/upload/export and browser product checks remain separate evidence. It does not claim another end-to-end Replay UI trial.

```powershell
.\.venv\Scripts\python.exe scripts/controlled_pilot.py evaluate --results-dir data/pilot-replay-01
```

This reuses the committed source recordings and frozen references, produces a separate result directory, and refuses to overwrite an existing evaluation. Model-derived frame/index work remains under ignored `data/controlled-pilot-v1`. Treat a repeated run as reproduction of these same two recordings, not additional independent test data. Exact timings vary, and numerical/library variation must be reported rather than concealed by changing the frozen references.

To recreate the **workflow** in new recordings, obtain `RiverHe2000/advice-ai-lab` at the source commit declared in the protocol and use that repository's installed Python environment (it provides `filenote`, Uvicorn and Playwright):

```powershell
# Run from replay-studio; supply paths appropriate to the local installation.
..\advice-ai-lab\.venv\Scripts\python.exe scripts/record_controlled_pilot.py --output data/new-controlled-capture --chromium PATH_TO_CHROMIUM
```

The actual capture used Chromium `1234` already installed on the host; each source receipt records the full browser version. New recording times and bytes naturally differ. Fresh recordings require fresh reference annotation and review before any model evaluation; the committed old intervals must not be reused for new footage. The annotation helper is intentionally unable to replace reviewed references. No model or application setup is needed simply to play and inspect the committed videos.
