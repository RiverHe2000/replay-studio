# Controlled browser pilot: measured result

Recorded and evaluated on 2026-09-27. Two real Chromium viewport recordings contain **139.28 seconds of silent software interaction** over synthetic meeting content. An AI operated the browser; there were no human participants. Eight answerable screen-only queries and two absent-feature queries were frozen before recording. Full visibility windows were then checked against state logs and video frames and committed before model evaluation. Both clips belong to **one application/source group**.

The result is mixed: fusion located one additional queried visual state, but also returned a false positive for an absent feature. The secondary evidence-time diagnostic measures finding a moment; it does not establish clip quality. **This pilot does not justify a general fusion-quality or user-productivity claim.** [Protocol and reproduction](CONTROLLED_RECORDING_PILOT.md) · [capture provenance](../artifacts/controlled-pilot-v1/PROVENANCE.md) · [pooled counts](../artifacts/controlled-pilot-v1/pooled.json).

## Frozen primary interval metrics

| Mode | Recall@1 / @3 / @5, IoU ≥ 0.3 | Mean top-1 IoU | Absent-feature false positives |
|---|---|---:|---:|
| Speech | 0/8 · 0/8 · 0/8 | 0.0000 | 0/2 |
| Speech + OCR | 0/8 · 0/8 · 0/8 | 0.0335 | 0/2 |
| Fusion | 0/8 · 0/8 · 0/8 | 0.0424 | 1/2 |

These primary recall values do **not** distinguish moment-finding quality here. The shortest complete reference visibility window is 10.100 seconds; returned candidates are about 2.5–3 seconds. Even a correctly located short candidate therefore cannot reach IoU 0.3 against that full state window. The windows were retained rather than shortened after scoring. This exposes a mismatch between full-state annotation and short-clip retrieval, not proof that no useful frame was found.

For all eight positive queries with a returned top hit, mean absolute start/end error to the nearest full reference interval was **16.447/32.668 seconds for OCR** and **20.514/28.168 seconds for fusion**. Misses are included. Speech returned no hits for all eight; its boundary errors are missing, not zero. Boundary quality remains unproven.

## Preregistered secondary point-localization diagnostic

An evidence-time hit means at least one actual evidence timestamp in the top *k* candidates falls inside any complete acceptable window. It does not imply a correct clip boundary, verified semantic entailment, or good editing output.

| Mode | Evidence-time hit@1 | @3 | @5 |
|---|---:|---:|---:|
| Speech | 0/8 | 0/8 | 0/8 |
| Speech + OCR | 2/8 | 3/8 | 3/8 |
| Fusion | 3/8 | 4/8 | 4/8 |

| Query/state | OCR hit@1 / @5 | Fusion hit@1 / @5 |
|---|---|---|
| Pasted transcript before drafting | no / yes | no / yes |
| Unsupported decision visible | no / no | no / no |
| Claim editor in use | no / no | no / no |
| Approved note and changed lines | yes / yes | yes / yes |
| Selected verified strategy | yes / yes | yes / yes |
| Highlighted transcript evidence | no / no | yes / yes |
| Needs-work comment entered | no / no | no / no |
| Feedback-recorded confirmation | no / no | no / no |

Fusion's one additional success is the highlighted transcript in the second recording. Its additional error is a visual candidate for **Kubernetes deployment configuration**, which never appears. Both routes correctly abstained on the absent credit-card checkout. The first recording's unsupported-decision query instead favored an earlier summary warning; claim-editor and feedback-state queries often favored the generic intake/feedback form. The measured candidates demonstrate the gap between matching UI vocabulary and locating the requested application state. No ranking, threshold, query or label was retuned.

Speech has no input because both sources are silent. Beating that empty baseline is expected by construction and says nothing about speech recognition or general multimodal performance. The two recordings are correlated, and there are only two negatives; no confidence interval or significance claim is reported.

## Measured CPU cost and identity

| Recording | Normalized duration | Sampled frames | OCR | CLIP stage | Total indexing |
|---|---:|---:|---:|---:|---:|
| Correction/approval | 69.833 s | 35 | 289.859 s | 18.047 s | 311.891 s |
| Evidence/feedback | 69.433 s | 35 | 263.750 s | 4.875 s | 272.656 s |
| Total | 139.267 s | 70 | 553.609 s | 22.922 s | **584.547 s** |

FFmpeg preparation is included in indexing; query evaluation is separate. Five queries took 0.141/0.156 seconds in the OCR route and 1.203/0.328 seconds in fusion for the first/second recording. The first fusion query includes text-model initialization and the second recording reused its cache. These are single observations on a shared workstation with other work in progress, not matched isolated performance trials or a service target. The very small silent-source/frame-rate normalization difference is within the frozen ±0.25-second reference timing uncertainty.

Actual CPU inference used Python 3.12.0, Torch 2.11.0+cu128 with CUDA hidden, RapidOCR 3.9.2, ONNX Runtime 1.29.0, Transformers 5.16.1 and NumPy 2.2.6. CLIP `openai/clip-vit-base-patch32` resolved to revision `3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268`; no new weights were downloaded. This is an existing development environment, not a new clean CPU-wheel installation test. [Runtime and OCR weight hashes](../artifacts/controlled-pilot-v1/runtime-identity.json) · [indexing summary](../artifacts/controlled-pilot-v1/summary.json).

## Audit trail and limits

- Final offline verification: **124 tests passed** in 18.10 seconds, including complete visibility recurrence, missing-sample rejection, evidence-time semantics and exact captured-byte receipt checks. Ruff and formatting checks passed. One upstream Starlette/AnyIO deprecation warning remained. No frontend/product behavior was changed by this experiment.
- [First recording](../artifacts/controlled-pilot-v1/correction-approval/source.webm): [references](../artifacts/controlled-pilot-v1/correction-approval/queries.json), [standard metrics](../artifacts/controlled-pilot-v1/correction-approval/evaluation.json), [full evidence diagnostics](../artifacts/controlled-pilot-v1/correction-approval/diagnostics.json), [actual OCR/model manifest](../artifacts/controlled-pilot-v1/correction-approval/manifest.json).
- [Second recording](../artifacts/controlled-pilot-v1/evidence-feedback/source.webm): [references](../artifacts/controlled-pilot-v1/evidence-feedback/queries.json), [standard metrics](../artifacts/controlled-pilot-v1/evidence-feedback/evaluation.json), [full evidence diagnostics](../artifacts/controlled-pilot-v1/evidence-feedback/diagnostics.json), [actual OCR/model manifest](../artifacts/controlled-pilot-v1/evidence-feedback/manifest.json).
- Model-produced OCR, embeddings and retrieval hits were never used as reference labels. Labels are AI-authored and AI-reviewed, with direct frame checks and a separate audit of every DOM-derived interval; they are not human/expert gold.
- Original synthetic-fixture reports and retrieval implementation remain unchanged. This pilot calls the actual analysis/retrieval modules directly; it does not duplicate the previous Replay upload/export UI validation.
- A future protocol should separately define state-location and clip-boundary tasks, freeze suitable metrics before collecting new footage, and use diverse sources and independent human annotation if broader claims are needed. This pilot's results must not be reused as a newly independent test after tuning.

The useful portfolio evidence is a reproducible software-recording experiment with frozen references, retained failures, explicit cost and a diagnosed metric limitation. It establishes neither user time savings nor deployment-scale readiness.
