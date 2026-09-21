import { useEffect, useRef, useState } from "react";
import {
  Check,
  FileVideo,
  Pause,
  Play,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react";
import { api, errorText, json } from "./api";
import type { UploadSession, UploadState } from "./types";
import { bytes, Notice, Spinner } from "./ui";

interface SavedUpload {
  id: string;
  name: string;
  size: number;
  modified: number;
  fingerprint: string;
  chunkSize: number;
}
async function fingerprint(file: File) {
  const samples = new Blob([
    file.slice(0, 262144),
    file.slice(Math.max(0, file.size / 2 - 131072), file.size / 2 + 131072),
    file.slice(Math.max(0, file.size - 262144)),
  ]);
  const digest = await crypto.subtle.digest(
    "SHA-256",
    await samples.arrayBuffer(),
  );
  return Array.from(new Uint8Array(digest), (value) =>
    value.toString(16).padStart(2, "0"),
  ).join("");
}
export default function Uploader({
  projectId,
  onComplete,
  onBusy,
}: {
  projectId: string;
  onComplete: () => void;
  onBusy: (busy: boolean) => void;
}) {
  const [state, setState] = useState<UploadState | null>(null);
  const [saved, setSaved] = useState<SavedUpload | null>(() => {
    try {
      return JSON.parse(
        localStorage.getItem(`replay.upload.${projectId}`) ?? "null",
      ) as SavedUpload | null;
    } catch {
      return null;
    }
  });
  const [drag, setDrag] = useState(false);
  const [discarding, setDiscarding] = useState(false);
  const [discardError, setDiscardError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const fileRef = useRef<File | null>(null);
  const abort = useRef<AbortController | null>(null);
  const running = useRef(false);
  const alive = useRef(true);
  const busyRef = useRef(onBusy);
  busyRef.current = onBusy;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      abort.current?.abort();
      busyRef.current(false);
    };
  }, []);
  const persist = (value: SavedUpload | null) => {
    setSaved(value);
    try {
      if (value)
        localStorage.setItem(
          `replay.upload.${projectId}`,
          JSON.stringify(value),
        );
      else localStorage.removeItem(`replay.upload.${projectId}`);
    } catch {
      /* Uploading still works when browser storage is disabled. */
    }
  };
  async function discardUpload() {
    if (!saved || running.current || discarding) return;
    if (
      !window.confirm(
        `Discard the unfinished upload “${saved.name}”? Uploaded chunks will be deleted and its reserved storage released. You will need to start the upload again.`,
      )
    )
      return;
    setDiscarding(true);
    setDiscardError("");
    onBusy(true);
    try {
      await api(`/uploads/${saved.id}`, { method: "DELETE" });
      // Clear the only resume handle only after the server confirms deletion.
      persist(null);
      fileRef.current = null;
      if (alive.current) setState(null);
    } catch (error) {
      if (alive.current) setDiscardError(errorText(error));
    } finally {
      if (alive.current) {
        setDiscarding(false);
        onBusy(false);
      }
    }
  }
  async function upload(file: File) {
    if (running.current || discarding) return;
    running.current = true;
    fileRef.current = file;
    setDiscardError("");
    onBusy(true);
    const controller = new AbortController();
    abort.current = controller;
    let offset = 0;
    setState({
      name: file.name,
      total: file.size,
      offset,
      status: "uploading",
    });
    try {
      if (!file.size)
        throw new Error("This file is empty. Choose a video recording.");
      const digest = await fingerprint(file);
      if (
        saved &&
        (saved.name !== file.name ||
          saved.size !== file.size ||
          saved.modified !== file.lastModified ||
          saved.fingerprint !== digest)
      ) {
        fileRef.current = null;
        throw new Error(
          `“${saved.name}” has an unfinished upload. Choose the same original file to resume it, or discard that upload before starting another.`,
        );
      }
      let session: UploadSession;
      if (
        saved &&
        saved.name === file.name &&
        saved.size === file.size &&
        saved.modified === file.lastModified &&
        saved.fingerprint === digest
      ) {
        const existing = await api<UploadSession>(`/uploads/${saved.id}`, {
          signal: controller.signal,
        });
        if (["completed", "complete"].includes(existing.status ?? "")) {
          persist(null);
          onComplete();
          setState(null);
          return;
        }
        session = { ...existing, chunk_size: saved.chunkSize };
      } else {
        session = await api<UploadSession>(`/projects/${projectId}/uploads`, {
          ...json("POST", { filename: file.name, size: file.size }),
          signal: controller.signal,
        });
        persist({
          id: session.id,
          name: file.name,
          size: file.size,
          modified: file.lastModified,
          fingerprint: digest,
          chunkSize: session.chunk_size,
        });
      }
      offset = session.offset;
      if (!Number.isFinite(offset) || offset < 0 || offset > file.size)
        throw new Error(
          "The saved upload has an invalid offset. Start a new upload.",
        );
      const chunkSize = Math.min(
        session.chunk_size || 4 * 1024 * 1024,
        4 * 1024 * 1024,
      );
      while (offset < file.size) {
        if (controller.signal.aborted)
          throw new DOMException("Upload paused", "AbortError");
        const chunk = file.slice(
          offset,
          Math.min(offset + chunkSize, file.size),
        );
        const response = await api<{ offset: number }>(
          `/uploads/${session.id}?offset=${offset}`,
          {
            method: "PUT",
            body: chunk,
            headers: { "Content-Type": "application/octet-stream" },
            signal: controller.signal,
          },
        );
        if (
          !Number.isFinite(response.offset) ||
          response.offset <= offset ||
          response.offset > file.size
        )
          throw new Error(
            "The server returned an invalid upload offset. Resume to check the upload.",
          );
        offset = response.offset;
        if (alive.current)
          setState({
            name: file.name,
            total: file.size,
            offset,
            status: "uploading",
          });
      }
      if (alive.current)
        setState({
          name: file.name,
          total: file.size,
          offset,
          status: "finalizing",
        });
      await api(`/uploads/${session.id}/complete`, {
        ...json("POST"),
        signal: controller.signal,
      });
      persist(null);
      if (alive.current) {
        setState({
          name: file.name,
          total: file.size,
          offset,
          status: "complete",
        });
        onComplete();
      }
    } catch (err) {
      if (alive.current)
        setState({
          name: file.name,
          total: file.size,
          offset,
          status: controller.signal.aborted ? "paused" : "error",
          ...(controller.signal.aborted ? {} : { error: errorText(err) }),
        });
    } finally {
      running.current = false;
      if (alive.current) onBusy(false);
    }
  }
  const active =
    state?.status === "uploading" || state?.status === "finalizing";
  return (
    <div className="upload-area">
      <input
        ref={input}
        className="visually-hidden"
        type="file"
        tabIndex={-1}
        disabled={discarding}
        accept="video/*,.mkv,.mov,.webm,.mp4"
        aria-label="Choose a video to upload"
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          event.currentTarget.value = "";
          if (file) void upload(file);
        }}
      />
      {!state || state.status === "complete" ? (
        <>
          <button
            className={`upload-drop ${drag ? "drag" : ""}`}
            disabled={discarding}
            onClick={() => input.current?.click()}
            onDragOver={(event) => {
              event.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDrag(false);
              const file = event.dataTransfer.files[0];
              if (file) void upload(file);
            }}
          >
            <UploadCloud size={19} />
            <span>
              Upload a recording<small>Drop a video or browse files</small>
            </span>
            <span className="upload-plus">+</span>
          </button>
          {state?.status === "complete" && (
            <p className="upload-success">
              <Check size={12} /> Upload complete. Analysis is queued.
            </p>
          )}
          {saved && (
            <p className="upload-hint">
              Resume “{saved.name}” by selecting the same original file.{" "}
              <button
                className="text-button danger"
                disabled={discarding}
                onClick={() => void discardUpload()}
              >
                {discarding ? (
                  <Spinner label="Discarding…" />
                ) : (
                  <>
                    <Trash2 size={10} />
                    Discard upload
                  </>
                )}
              </button>
            </p>
          )}
        </>
      ) : (
        <div className="upload-progress">
          <div className="upload-file">
            <FileVideo size={19} />
            <span>
              {state.name}
              <small>
                {bytes(state.offset)} / {bytes(state.total)}
              </small>
            </span>
            {active ? (
              <button
                className="icon-button"
                aria-label="Pause upload"
                title="Pause upload"
                onClick={() => abort.current?.abort()}
              >
                <Pause size={15} />
              </button>
            ) : (
              <button
                className="icon-button"
                aria-label="Dismiss upload status"
                disabled={discarding}
                onClick={() => setState(null)}
              >
                <X size={15} />
              </button>
            )}
          </div>
          <progress
            max={state.total || 1}
            value={state.offset}
            aria-label="Upload progress"
          />
          <div className="upload-state">
            {state.status === "finalizing" ? (
              <Spinner label="Checking recording…" />
            ) : state.status === "uploading" ? (
              `Uploading ${Math.round((state.offset / state.total) * 100)}%`
            ) : (
              <span>
                {state.status === "paused"
                  ? "Upload paused"
                  : "Upload interrupted"}{" "}
                <button
                  className="text-button"
                  disabled={discarding}
                  onClick={() =>
                    fileRef.current
                      ? void upload(fileRef.current)
                      : input.current?.click()
                  }
                >
                  <Play size={11} />{" "}
                  {fileRef.current ? "Resume" : "Choose original file"}
                </button>
              </span>
            )}
          </div>
          {state.error && <Notice warning>{state.error}</Notice>}
          {!active && saved && (
            <div className="discard-upload">
              <button
                className="text-button danger"
                disabled={discarding}
                onClick={() => void discardUpload()}
              >
                {discarding ? (
                  <Spinner label="Discarding…" />
                ) : (
                  <>
                    <Trash2 size={11} />
                    Discard upload
                  </>
                )}
              </button>
            </div>
          )}
        </div>
      )}
      {discardError && (
        <div className="discard-upload-error">
          <Notice warning>
            {discardError} Your resume information has been kept.
          </Notice>
        </div>
      )}
    </div>
  );
}
