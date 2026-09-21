import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronRight,
  Clock3,
  GripVertical,
  Play,
  Plus,
  Save,
  Scissors,
  Trash2,
  Undo2,
} from "lucide-react";
import type { Clip, Timeline } from "./types";
import { Notice, Spinner, timecode } from "./ui";

export const timelineSignature = (timeline: Timeline) =>
  JSON.stringify({
    asset_id: timeline.asset_id,
    run_id: timeline.run_id,
    clips: timeline.clips,
  });
export function validateTimeline(
  timeline: Timeline,
  sourceDuration?: number | null,
) {
  if (timeline.clips.length > 30)
    return "A timeline can contain up to 30 clips.";
  for (const [index, clip] of timeline.clips.entries()) {
    if (
      ![clip.start, clip.end].every(Number.isFinite) ||
      clip.start < 0 ||
      clip.end - clip.start < 0.1
    )
      return `Clip ${index + 1} must be at least 0.1 seconds long, with a non-negative start.`;
    if (sourceDuration && clip.end > sourceDuration + 0.001)
      return `Clip ${index + 1} extends past the end of the recording.`;
    if (clip.title.length > 200 || clip.subtitle.length > 2000)
      return `Clip ${index + 1} has text that is too long.`;
  }
  if (
    timeline.clips.reduce((sum, clip) => sum + clip.end - clip.start, 0) > 600
  )
    return "Keep the total edit under 10 minutes.";
  return "";
}
export default function TimelineEditor({
  timeline,
  selected,
  onSelect,
  onChange,
  onUndo,
  canUndo,
  onSave,
  saving,
  dirty,
  canEdit,
  onPreview,
  onAdd,
  sourceName,
  sourceDuration,
  conflict,
  onResolve,
}: {
  timeline: Timeline;
  selected: string | null;
  onSelect: (id: string) => void;
  onChange: (timeline: Timeline) => void;
  onUndo: () => void;
  canUndo: boolean;
  onSave: () => void;
  saving: boolean;
  dirty: boolean;
  canEdit: boolean;
  onPreview: (id?: string) => void;
  onAdd: () => void;
  sourceName?: string;
  sourceDuration?: number | null;
  conflict: boolean;
  onResolve: () => void;
}) {
  const total = timeline.clips.reduce(
    (sum, clip) => sum + Math.max(0, clip.end - clip.start),
    0,
  );
  const invalid = validateTimeline(timeline, sourceDuration);
  const current = timeline.clips.find((clip) => clip.id === selected);
  const index = timeline.clips.findIndex((clip) => clip.id === selected);
  function patch(id: string, value: Partial<Clip>) {
    onChange({
      ...timeline,
      clips: timeline.clips.map((clip) =>
        clip.id === id ? { ...clip, ...value } : clip,
      ),
    });
  }
  function move(from: number, direction: number) {
    const to = from + direction;
    if (to < 0 || to >= timeline.clips.length) return;
    const clips = [...timeline.clips];
    [clips[from], clips[to]] = [clips[to], clips[from]];
    onChange({ ...timeline, clips });
  }
  return (
    <section className="timeline-panel" aria-label="Timeline editor">
      <div className="timeline-toolbar">
        <div className="panel-title">
          <Scissors size={16} />
          <h2>Your cut</h2>
          <span className="count-chip">
            {timeline.clips.length}{" "}
            {timeline.clips.length === 1 ? "clip" : "clips"}
          </span>
        </div>
        <div className="toolbar-actions">
          <span className="timeline-duration">
            <Clock3 size={12} />
            {timecode(total)}
            <span> / 10:00 max</span>
          </span>
          <span className={`save-status ${dirty ? "unsaved" : ""}`}>
            {dirty ? (
              "Unsaved changes"
            ) : (
              <>
                <Check size={12} /> Saved · v{timeline.version}
              </>
            )}
          </span>
          <button
            className="icon-button"
            title="Undo (Ctrl/⌘ Z)"
            aria-label="Undo last edit"
            disabled={!canUndo || !canEdit}
            onClick={onUndo}
          >
            <Undo2 size={16} />
          </button>
          <button
            className="button small"
            disabled={!timeline.clips.length || !!invalid}
            onClick={() => onPreview()}
          >
            <Play size={12} />
            Preview
          </button>
          <button
            className="button small primary"
            disabled={
              !dirty || saving || !canEdit || !!invalid || !timeline.asset_id
            }
            onClick={onSave}
          >
            {saving ? (
              <Spinner label="Saving" />
            ) : (
              <>
                <Save size={13} />
                Save version
              </>
            )}
          </button>
        </div>
      </div>
      {conflict && (
        <div className="timeline-conflict">
          <Notice warning>
            A newer version is available. Your edits are kept locally.{" "}
            <button className="text-button" onClick={onResolve}>
              Review conflict
            </button>
          </Notice>
        </div>
      )}
      {invalid && (
        <div className="timeline-conflict">
          <Notice warning>{invalid}</Notice>
        </div>
      )}
      {timeline.clips.length ? (
        <>
          <div className="timeline-track-wrap">
            <div className="track-label">
              <span>VIDEO 01</span>
              <span>{sourceName ?? "Source recording"}</span>
            </div>
            <div className="timeline-ruler" aria-hidden="true">
              {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
                <span key={fraction}>{timecode(total * fraction)}</span>
              ))}
            </div>
            <div className="timeline-track">
              {timeline.clips.map((clip, i) => (
                <button
                  key={clip.id}
                  className={`timeline-block ${selected === clip.id ? "selected" : ""}`}
                  style={{
                    flexGrow: Math.max(1, clip.end - clip.start),
                    flexBasis: 0,
                  }}
                  onClick={() => onSelect(clip.id)}
                  onDoubleClick={() => onPreview(clip.id)}
                  onKeyDown={(event) => {
                    if (
                      event.altKey &&
                      (event.key === "ArrowLeft" ||
                        event.key === "ArrowRight") &&
                      canEdit
                    ) {
                      event.preventDefault();
                      move(i, event.key === "ArrowLeft" ? -1 : 1);
                    }
                  }}
                  title={`${clip.title || `Clip ${i + 1}`} · ${timecode(clip.start)}–${timecode(clip.end)}. Alt + Left/Right to reorder.`}
                >
                  <span className="clip-handle" />
                  <span className="timeline-block-num">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <strong>{clip.title || `Clip ${i + 1}`}</strong>
                  <small>{timecode(clip.end - clip.start)}</small>
                  <span className="clip-handle end" />
                </button>
              ))}
              {canEdit && (
                <button
                  className="timeline-add"
                  onClick={onAdd}
                  aria-label="Add a clip at the playhead"
                >
                  <Plus size={20} />
                </button>
              )}
            </div>
          </div>
          <div className="clip-editor-area">
            <div className="clip-list" aria-label="Clip sequence">
              {timeline.clips.map((clip, i) => (
                <div
                  key={clip.id}
                  className={`clip-list-row ${clip.id === selected ? "selected" : ""}`}
                >
                  <button
                    className="clip-list-select"
                    onClick={() => onSelect(clip.id)}
                  >
                    <span className="clip-number">
                      {String(i + 1).padStart(2, "0")}
                    </span>
                    <span>
                      {clip.title || `Clip ${i + 1}`}
                      <small>
                        {timecode(clip.start)} <span>→</span>{" "}
                        {timecode(clip.end)}
                      </small>
                    </span>
                    <ChevronRight size={13} />
                  </button>
                  <div className="clip-reorder">
                    <button
                      className="icon-button tiny"
                      disabled={!canEdit || i === 0}
                      onClick={() => move(i, -1)}
                      aria-label={`Move clip ${i + 1} earlier`}
                    >
                      <ArrowUp size={12} />
                    </button>
                    <button
                      className="icon-button tiny"
                      disabled={!canEdit || i === timeline.clips.length - 1}
                      onClick={() => move(i, 1)}
                      aria-label={`Move clip ${i + 1} later`}
                    >
                      <ArrowDown size={12} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
            {current ? (
              <div className="clip-inspector">
                <div className="inspector-head">
                  <span className="eyebrow">
                    <GripVertical size={12} /> CLIP{" "}
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <span className="source-range">
                    {timecode(current.end - current.start, true)} selected
                  </span>
                  <button
                    className="icon-button tiny danger"
                    aria-label="Remove selected clip"
                    disabled={!canEdit}
                    onClick={() =>
                      onChange({
                        ...timeline,
                        clips: timeline.clips.filter(
                          (clip) => clip.id !== current.id,
                        ),
                      })
                    }
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
                <div className="clip-fields">
                  <label className="clip-title-label">
                    Clip title
                    <input
                      value={current.title}
                      maxLength={200}
                      disabled={!canEdit}
                      onChange={(event) =>
                        patch(current.id, { title: event.currentTarget.value })
                      }
                      placeholder="Give this moment a name"
                    />
                  </label>
                  <label>
                    In <span className="unit">seconds</span>
                    <input
                      type="number"
                      min={0}
                      step={0.1}
                      value={current.start}
                      disabled={!canEdit}
                      onChange={(event) =>
                        patch(current.id, {
                          start:
                            event.currentTarget.value === ""
                              ? 0
                              : Number(event.currentTarget.value),
                        })
                      }
                    />
                  </label>
                  <label>
                    Out <span className="unit">seconds</span>
                    <input
                      type="number"
                      min={0}
                      step={0.1}
                      value={current.end}
                      disabled={!canEdit}
                      onChange={(event) =>
                        patch(current.id, {
                          end:
                            event.currentTarget.value === ""
                              ? 0
                              : Number(event.currentTarget.value),
                        })
                      }
                    />
                  </label>
                </div>
                <label className="subtitle-label">
                  Clip caption <span>Included in the subtitle file</span>
                  <textarea
                    rows={2}
                    value={current.subtitle}
                    maxLength={2000}
                    disabled={!canEdit}
                    onChange={(event) =>
                      patch(current.id, { subtitle: event.currentTarget.value })
                    }
                    placeholder="A concise caption for this clip…"
                  />
                </label>
              </div>
            ) : (
              <div className="clip-inspector idle">
                <Scissors size={20} />
                <p>Select a clip to adjust its boundaries and caption.</p>
              </div>
            )}
          </div>
        </>
      ) : (
        <div className="timeline-empty">
          <div className="empty-track" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <div>
            <h3>The good parts go here.</h3>
            <p>
              Add an evidence-backed moment, draft an edit from your search, or
              mark a clip yourself.
            </p>
          </div>
          <button className="button small" disabled={!canEdit} onClick={onAdd}>
            <Plus size={14} />
            Add at playhead
          </button>
        </div>
      )}
    </section>
  );
}
