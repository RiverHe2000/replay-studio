# Evaluation protocol and pending user research

The included generated video is an integration fixture, visibly labelled on every frame. It is not a real incident, not an unseen test set and not a substitute for a user study. The script never inserts its expected labels into the analyzer.

## Dataset protocol

The original target remains 20–30 authorized recordings, roughly 8–15 hours, and 150 human-written queries. No such dataset has been collected by this implementation. Obtain permission before retaining or publishing recordings; record source, license/consent, speaker/source group, duration and hash. Keep private source material outside Git.

Freeze development and held-out test groups before tuning. A source recording, adjacent excerpt, speaker or near-duplicate belongs to only one group/split. Include speech-only, screen-only, joint audio/visual, temporal ordering, and unanswerable tasks. Two annotators should independently check a subset and record disagreements, alternate correct intervals and evidence type. Do not use model-generated descriptions as gold labels.

Each manifest is evaluated separately. Queries use this shape (illustrative, not measured data):

```json
{
  "id": "source-01-q03",
  "asset_id": "actual_asset_id",
  "group": "source_or_speaker_group_01",
  "split": "test",
  "type": "screen_only",
  "query": "Where does the recording display the missing database setting?",
  "answerable": true,
  "intervals": [{"start": 12.0, "end": 17.0}]
}
```

For unanswerable queries use `answerable: false` and `intervals: []`. The evaluator rejects cross-split group leakage within its input and mixed assets; the dataset curator must also enforce those constraints across separate evaluation invocations.

```sh
python -m replay_studio.evaluation --manifest manifest.json --base materialized_run --queries queries.json --output evaluation.json
```

It runs identical queries against speech, speech+OCR and fusion, and records Recall@1/3/5 at IoU≥0.3, top-1 IoU, correct abstention/false positives, per-type results, source-group macro recall, individual hits, query time, manifest/data hashes and environment. Aggregate by source video before reporting confidence intervals. Current evaluation records IoU rather than a separate boundary-error statistic; annotate and report start/end errors additionally for real datasets. Time and GPU memory should be measured with fixed warm/cold conditions, video duration, sample rate, model revisions and concurrency.

## Go/no-go criteria

Freeze criteria before testing: screen-only recall must exceed the speech baseline on independently annotated footage; false-positive changes and additional indexing latency must be reported alongside any improvement. A negative result is useful: keep the cheaper route as default if the visual route adds no dependable value. Check multi-event editing separately from interval retrieval; finding relevant frames does not establish causality or produce a good narrative.

## User-study protocol (not conducted)

Recruit 5–8 consenting developers/researchers/tutorial authors. Assign comparable editing tasks with counterbalanced tool order. Compare their usual workflow against Replay Studio on distinct comparable recordings. Separate cold upload/indexing wait from already-indexed interaction, and also report total elapsed time.

Record per participant: task completion, total time, wrong/missing events, adjusted boundaries, rejected suggestions, final adoption, waiting time and written reasons. Retain raw observations with pseudonymous IDs. Ask for consent to publish footage or quotes separately. No emails or invitations have been sent by this implementation.

Only after these sessions should the portfolio claim user time savings. Until then, an accurate claim is that the project implements and tests a multimodal media-processing product with traceable evidence and failure recovery.
