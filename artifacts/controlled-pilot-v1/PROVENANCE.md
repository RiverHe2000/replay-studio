# Capture and reference provenance

- Protocol frozen in `583fc93` before recording.
- Source recordings, event logs and visually reviewed labels frozen in `3abe624` before invoking any Replay Studio model.
- Actual Chromium viewport capture: two silent recordings, 69.84 and 69.44 seconds, one shared application/source group. The application backend and meeting contents are explicitly scripted/synthetic; the recorded browser interaction is real.
- Direct AI frame review covered transition-adjacent frames, interval interiors, systematic samples and final encoded frames. A separate root AI agent additionally reviewed both second contact sheets and confirmed the correction, approval, feedback and persistent-highlight states. Neither review is human annotation or participant evidence.
- A separate audit AI agent independently recomputed all ten reference windows from the complete DOM runs and clock offsets, including recurrence and final-state extensions. It also recomputed the first recording's per-query evidence-time scores and confirmed the denominators, negative false positive and distinction from interval IoU. This is code/data review, not a second human label set.
- `clock-alignment.json` documents the pre-model correction from DOM clock to video time. It uses visually read instrumentation, not OCR predictions. Final labels retain every full visible interval and later recurrence. The frozen uncertainty is ±0.25 seconds.

## Byte preservation correction

The Windows capture helpers wrote JSON with CRLF endings, and receipt hashes describe those original bytes. The first reference commit inherited the repository's LF normalization, which changed the Git representation of JSON while leaving capture files unchanged locally. A subsequent attributes-only preservation rule and re-addition retain the original captured JSON bytes across platforms. No content, query, reference interval, source video or recorded hash was changed. The original freeze commit remains in history; its parsed JSON is identical to the byte-preserved representation. A regression test validates each receipt against committed artifacts, including after a clean checkout.

Model evaluation began only after the reference freeze; this serialization correction does not derive from or change model quality results. Future JSON in this artifact directory is also retained byte-for-byte.

## Execution scope

Only locally cached model weights are used. CUDA visibility is empty, Replay's model device is CPU, optional VLM and planner are disabled, and Hugging Face operates offline. The two-thread setting applies to PyTorch and FFmpeg; RapidOCR uses its installed ONNX Runtime default thread configuration. Timing reflects this workstation and other concurrent work, not an isolated performance benchmark. No GPU model task, paid service or cloud deployment is involved.
