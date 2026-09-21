import {
  AudioLines,
  Captions,
  ChevronRight,
  Clock3,
  Plus,
  ScanLine,
  Search,
  Sparkles,
} from "lucide-react";
import type { Hit, Mode, Transcript } from "./types";
import { frameUrl } from "./api";
import { Empty, Notice, Spinner, timecode } from "./ui";

const modalityIcon = (modality: string) =>
  modality === "speech" ? (
    <AudioLines size={11} />
  ) : modality === "screen" ? (
    <Captions size={11} />
  ) : (
    <ScanLine size={11} />
  );
export default function EvidencePanel({
  assetId,
  query,
  setQuery,
  mode,
  setMode,
  onSearch,
  hits,
  searchBusy,
  searchDone,
  searchWarnings,
  onSeek,
  onAdd,
  canEdit,
  onPlan,
  planBusy,
  transcript,
  transcriptBusy,
  tab,
  setTab,
  ready,
}: {
  assetId?: string;
  query: string;
  setQuery: (value: string) => void;
  mode: Mode;
  setMode: (value: Mode) => void;
  onSearch: () => void;
  hits: Hit[];
  searchBusy: boolean;
  searchDone: boolean;
  searchWarnings: string[];
  onSeek: (time: number, end?: number) => void;
  onAdd: (hit: Hit) => void;
  canEdit: boolean;
  onPlan: () => void;
  planBusy: boolean;
  transcript: Transcript | null;
  transcriptBusy: boolean;
  tab: "search" | "transcript";
  setTab: (tab: "search" | "transcript") => void;
  ready: boolean;
}) {
  return (
    <aside className="evidence-panel">
      <div
        className="evidence-tabs"
        role="tablist"
        aria-label="Source exploration"
      >
        <button
          role="tab"
          aria-selected={tab === "search"}
          className={tab === "search" ? "active" : ""}
          onClick={() => setTab("search")}
        >
          <Search size={14} />
          Find a moment
        </button>
        <button
          role="tab"
          aria-selected={tab === "transcript"}
          className={tab === "transcript" ? "active" : ""}
          onClick={() => setTab("transcript")}
        >
          <AudioLines size={14} />
          Transcript
        </button>
      </div>
      {tab === "search" ? (
        <div className="search-content">
          <div className="search-intro">
            <span className="eyebrow">SEARCH THE RECORDING</span>
            <h2>What are you looking for?</h2>
            <p>Find a phrase, something on screen, or a visual moment.</p>
          </div>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              onSearch();
            }}
            className="search-form"
          >
            <label className="visually-hidden" htmlFor="moment-query">
              Describe a moment
            </label>
            <textarea
              id="moment-query"
              placeholder={"Try “the error message before the successful run”"}
              value={query}
              maxLength={1000}
              onChange={(event) => setQuery(event.currentTarget.value)}
              rows={3}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  if (query.trim() && ready && !searchBusy) onSearch();
                }
              }}
            />
            <div className="search-form-bottom">
              <label className="search-mode">
                <ScanLine size={13} />
                <select
                  value={mode}
                  onChange={(event) =>
                    setMode(event.currentTarget.value as Mode)
                  }
                  aria-label="Search mode"
                >
                  <option value="fusion">All evidence</option>
                  <option value="speech_ocr">Speech + screen text</option>
                  <option value="speech">Speech only</option>
                </select>
              </label>
              <button
                type="submit"
                className="search-submit"
                disabled={!query.trim() || !ready || searchBusy}
                aria-label="Search recording"
              >
                {searchBusy ? (
                  <span className="spin">
                    <Search size={16} />
                  </span>
                ) : (
                  <Search size={16} />
                )}
              </button>
            </div>
          </form>
          {searchWarnings.map((warning, i) => (
            <Notice warning key={`${i}-${warning}`}>
              {warning}
            </Notice>
          ))}
          <div className="results-heading">
            <span>
              {searchBusy
                ? "FINDING MOMENTS"
                : searchDone
                  ? `${hits.length} MATCHING ${hits.length === 1 ? "MOMENT" : "MOMENTS"}`
                  : "EVIDENCE, NOT GUESSWORK"}
            </span>
            {searchDone && hits.length > 0 && (
              <button
                className="text-button"
                disabled={!canEdit || planBusy || !ready}
                onClick={onPlan}
              >
                <Sparkles size={12} />
                {planBusy ? "Drafting…" : "Draft an edit"}
              </button>
            )}
          </div>
          <div
            className="evidence-results"
            aria-live="polite"
            aria-busy={searchBusy}
          >
            {searchBusy ? (
              <div className="result-loading">
                <Spinner label="Searching the source…" />
              </div>
            ) : hits.length ? (
              hits.map((hit, index) => (
                <article className="evidence-card" key={hit.id}>
                  <button
                    className="evidence-seek"
                    onClick={() => onSeek(hit.start, hit.end)}
                    aria-label={`Play result ${index + 1} at ${timecode(hit.start)}`}
                  >
                    <div className="evidence-image">
                      {hit.frame && assetId ? (
                        <img
                          loading="lazy"
                          src={frameUrl(assetId, hit.frame)}
                          alt={`Source frame at ${timecode(hit.start)}`}
                          onError={(event) => {
                            event.currentTarget.style.display = "none";
                          }}
                        />
                      ) : (
                        <AudioLines size={26} strokeWidth={1} />
                      )}
                      <span className="evidence-time">
                        {timecode(hit.start)}
                      </span>
                    </div>
                    <div className="evidence-summary">
                      <span className="result-index">
                        MOMENT {String(index + 1).padStart(2, "0")}
                        <ChevronRight size={12} />
                      </span>
                      <p>{hit.text || "A visual match in the recording."}</p>
                    </div>
                  </button>
                  <div className="evidence-meta">
                    <div className="modality-tags">
                      {hit.modalities.map((modality) => (
                        <span key={modality}>
                          {modalityIcon(modality)}
                          {modality === "screen"
                            ? "Screen text"
                            : modality === "speech"
                              ? "Speech"
                              : "Visual"}
                        </span>
                      ))}
                    </div>
                    <button
                      className="icon-button tiny add-moment"
                      disabled={!canEdit}
                      onClick={() => onAdd(hit)}
                      aria-label={`Add moment ${index + 1} to timeline`}
                      title="Add to timeline"
                    >
                      <Plus size={15} />
                    </button>
                  </div>
                  {hit.uncertain && (
                    <p className="uncertain-note">
                      Partial evidence — review this moment.
                    </p>
                  )}
                  {hit.evidence.length > 0 && (
                    <details className="evidence-details">
                      <summary>
                        View source evidence <span>{hit.evidence.length}</span>
                      </summary>
                      {hit.evidence.map((evidence, i) => (
                        <button
                          key={i}
                          className="evidence-quote"
                          onClick={() => onSeek(evidence.time)}
                        >
                          <span>
                            {modalityIcon(evidence.modality)}
                            {timecode(evidence.time)}
                          </span>
                          <p>{evidence.text}</p>
                        </button>
                      ))}
                    </details>
                  )}
                </article>
              ))
            ) : searchDone ? (
              <Empty
                title="No supported matches"
                detail="Try a specific phrase, screen label, or a different evidence mode."
              />
            ) : (
              <div className="search-empty">
                <div className="evidence-orbit" aria-hidden="true">
                  <span>
                    <AudioLines size={19} />
                  </span>
                  <span>
                    <Captions size={19} />
                  </span>
                  <span>
                    <ScanLine size={19} />
                  </span>
                </div>
                <h3>
                  {!assetId
                    ? "Start with a recording"
                    : !ready
                      ? "Your source is getting ready"
                      : "Every moment has a source."}
                </h3>
                <p>
                  {!assetId
                    ? "Upload a recording to search its speech, screen text, and visuals."
                    : !ready
                      ? "Search becomes available after analysis. Stage progress is shown in your media library."
                      : "Matches link back to the original recording, with speech and visual evidence kept separate."}
                </p>
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="transcript-content">
          {transcriptBusy ? (
            <Spinner label="Loading transcript…" />
          ) : transcript && transcript.segments.length ? (
            <>
              <div className="transcript-info">
                <span className="eyebrow">SOURCE TRANSCRIPT</span>
                <span>{transcript.backend}</span>
              </div>
              {transcript.segments.map((segment, index) => (
                <button
                  className="transcript-segment"
                  key={`${segment.start}-${index}`}
                  onClick={() => onSeek(segment.start, segment.end)}
                >
                  <span>
                    <Clock3 size={11} />
                    {timecode(segment.start)}
                  </span>
                  <p>{segment.text}</p>
                </button>
              ))}
            </>
          ) : (
            <Empty
              title="No transcript yet"
              detail={
                transcript?.status === "unavailable"
                  ? "Speech recognition is unavailable for this analysis. Other evidence may still be searchable."
                  : transcript?.status === "skipped"
                    ? "Speech recognition was disabled for this analysis."
                    : "A transcript appears here when speech recognition has finished."
              }
            />
          )}
        </div>
      )}
    </aside>
  );
}
