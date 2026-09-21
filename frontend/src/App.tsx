import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowDownToLine,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Clock3,
  Download,
  Ellipsis,
  FileText,
  Film,
  FolderOpen,
  History,
  LayoutGrid,
  LogOut,
  MessageSquare,
  MonitorPlay,
  Pause,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Scissors,
  Settings2,
  Shield,
  SkipBack,
  Sparkles,
  Trash2,
  Users,
  Volume2,
  X,
} from "lucide-react";
import {
  api,
  ApiError,
  errorText,
  frameUrl,
  json,
  mediaUrl,
  setCsrf,
} from "./api";
import AuthScreen from "./AuthScreen";
import EvidencePanel from "./EvidencePanel";
import TimelineEditor, {
  timelineSignature,
  validateTimeline,
} from "./TimelineEditor";
import Uploader from "./Uploader";
import {
  activeStatus,
  Brand,
  bytes,
  dateLabel,
  doneStatus,
  Empty,
  Modal,
  Notice,
  Spinner,
  Status,
  timecode,
} from "./ui";
import type {
  Asset,
  Auth,
  CapabilityMap,
  Clip,
  Export,
  Hit,
  Mode,
  Plan,
  Project,
  ProjectSummary,
  Timeline,
  Transcript,
} from "./types";

const emptyTimeline = (): Timeline => ({
  version: 0,
  asset_id: null,
  run_id: null,
  clips: [],
});
type Dialog =
  | "new"
  | "team"
  | "comments"
  | "exports"
  | "history"
  | "capabilities"
  | "analyze"
  | "conflict"
  | "plan"
  | null;
function capabilityText(value: unknown): string {
  if (typeof value === "boolean") return value ? "Available" : "Unavailable";
  if (typeof value === "string" || typeof value === "number")
    return String(value);
  if (value == null) return "Not reported";
  if (typeof value === "object") {
    const entry = value as Record<string, unknown>;
    if (typeof entry.status === "string") return entry.status;
    if (typeof entry.available === "boolean")
      return entry.available ? "Available" : "Unavailable";
    return JSON.stringify(value);
  }
  return "Not reported";
}

export default function App() {
  const [booting, setBooting] = useState(true);
  const [bootError, setBootError] = useState("");
  const [auth, setAuth] = useState<Auth | null>(null);
  const lastUserId = useRef<string | null>(null);
  const [authStatus, setAuthStatus] = useState({
    needs_setup: false,
    registration_open: false,
  });
  const [capabilities, setCapabilities] = useState<CapabilityMap>({});
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(false);
  const [projectId, setProjectId] = useState<string | null>(null);
  const projectIdRef = useRef<string | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [loadingProject, setLoadingProject] = useState(false);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const selectedAssetRef = useRef<string | null>(null);
  const [editor, setEditorState] = useState<Timeline>(emptyTimeline);
  const editorRef = useRef(editor);
  const baselineRef = useRef(timelineSignature(editor));
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);
  const editRevision = useRef(0);
  const [undoHistory, setUndoHistory] = useState<Timeline[]>([]);
  const [selectedClip, setSelectedClip] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<Mode>("fusion");
  const [hits, setHits] = useState<Hit[]>([]);
  const [searchBusy, setSearchBusy] = useState(false);
  const [searchDone, setSearchDone] = useState(false);
  const [searchWarnings, setSearchWarnings] = useState<string[]>([]);
  const [planBusy, setPlanBusy] = useState(false);
  const [draft, setDraft] = useState<Plan | null>(null);
  const [targetSeconds, setTargetSeconds] = useState(180);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [transcriptBusy, setTranscriptBusy] = useState(false);
  const [evidenceTab, setEvidenceTab] = useState<"search" | "transcript">(
    "search",
  );
  const [dialog, setDialog] = useState<Dialog>(null);
  const [dialogBusy, setDialogBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [versions, setVersions] = useState<Timeline[]>([]);
  const [analysisOptions, setAnalysisOptions] = useState({
    asr: true,
    ocr: true,
    visual: true,
  });
  const [assetMenu, setAssetMenu] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const video = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [sourceTime, setSourceTime] = useState(0);
  const [mediaDuration, setMediaDuration] = useState(0);
  const [mediaError, setMediaError] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const previewQueue = useRef<Clip[]>([]);
  const pendingSeek = useRef<{ time: number; play: boolean } | null>(null);
  const searchSequence = useRef(0);
  const polling = useRef(false);
  const asset = project?.assets.find((item) => item.id === selectedAssetId);
  const sourceAsset = project?.assets.find(
    (item) => item.id === editor.asset_id,
  );
  const canEdit = !!project && project.role !== "viewer";
  const owner = project?.role === "owner";
  const ready = !!asset && doneStatus(asset.status);
  const activeJobs =
    project?.jobs.filter((job) => activeStatus(job.status)) ?? [];
  const totalDuration = editor.clips.reduce(
    (sum, clip) => sum + Math.max(0, clip.end - clip.start),
    0,
  );

  const putEditor = useCallback((next: Timeline, baseline = false) => {
    editorRef.current = next;
    if (baseline) baselineRef.current = timelineSignature(next);
    const changed = timelineSignature(next) !== baselineRef.current;
    dirtyRef.current = changed;
    setDirty(changed);
    setEditorState(next);
    setSelectedClip((previous) =>
      next.clips.some((clip) => clip.id === previous)
        ? previous
        : (next.clips[0]?.id ?? null),
    );
  }, []);
  function edit(next: Timeline) {
    setUndoHistory((history) => [
      ...history.slice(-49),
      structuredClone(editorRef.current),
    ]);
    editRevision.current += 1;
    putEditor(next);
  }
  function undo() {
    const previous = undoHistory[undoHistory.length - 1];
    if (!previous || !canEdit) return;
    setUndoHistory((history) => history.slice(0, -1));
    editRevision.current += 1;
    putEditor({ ...previous, version: editorRef.current.version });
  }
  function showDialog(value: Dialog) {
    setDialogError("");
    setDialog(value);
  }
  const notify = (message: string) => setToast(message);

  const bootstrap = useCallback(async () => {
    setBooting(true);
    setBootError("");
    try {
      const [status, me, caps] = await Promise.allSettled([
        api<typeof authStatus>("/auth/status"),
        api<Auth>("/auth/me"),
        api<CapabilityMap>("/capabilities"),
      ]);
      if (status.status === "rejected") throw status.reason;
      setAuthStatus(status.value);
      if (caps.status === "fulfilled") setCapabilities(caps.value);
      if (me.status === "fulfilled") {
        setCsrf(me.value.csrf_token);
        lastUserId.current = me.value.user.id;
        setAuth(me.value);
      } else if (!(me.reason instanceof ApiError && me.reason.status === 401))
        throw me.reason;
    } catch (err) {
      setBootError(errorText(err));
    } finally {
      setBooting(false);
    }
  }, []);
  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);
  useEffect(() => {
    const expired = () => {
      setAuth(null);
      setCsrf("");
      setError(
        "Your session expired. Sign in again; your local edit is preserved.",
      );
    };
    window.addEventListener("replay:session-expired", expired);
    return () => window.removeEventListener("replay:session-expired", expired);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 5500);
    return () => clearTimeout(timer);
  }, [toast]);
  const loadProjects = useCallback(async () => {
    setProjectsLoading(true);
    try {
      setProjects(await api<ProjectSummary[]>("/projects"));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setProjectsLoading(false);
    }
  }, []);
  useEffect(() => {
    if (auth) void loadProjects();
  }, [auth, loadProjects]);
  const refreshProject = useCallback(
    async (id: string, initial = false) => {
      if (initial) setLoadingProject(true);
      try {
        const next = await api<Project>(`/projects/${id}`);
        if (projectIdRef.current !== id) return;
        next.assets ??= [];
        next.jobs ??= [];
        next.exports ??= [];
        next.members ??= [];
        next.comments ??= [];
        next.timeline ??= emptyTimeline();
        setProject(next);
        setProjects((items) =>
          items.map((item) =>
            item.id === id
              ? {
                  ...item,
                  name: next.name,
                  role: next.role,
                  updated_at: next.updated_at,
                }
              : item,
          ),
        );
        setSelectedAssetId((previous) =>
          next.assets.some((item) => item.id === previous)
            ? previous
            : (next.assets.find((item) => item.id === next.timeline.asset_id)
                ?.id ??
              next.assets[0]?.id ??
              null),
        );
        // A poll that started before a successful save can arrive afterwards.
        // Never roll the editor back to that older snapshot.
        if (next.timeline.version >= editorRef.current.version) {
          if (!dirtyRef.current) {
            putEditor(next.timeline, true);
            setConflict(false);
          } else if (next.timeline.version !== editorRef.current.version)
            setConflict(true);
        }
      } catch (err) {
        if (projectIdRef.current === id) setError(errorText(err));
      } finally {
        if (initial && projectIdRef.current === id) setLoadingProject(false);
      }
    },
    [putEditor],
  );
  useEffect(() => {
    projectIdRef.current = projectId;
    if (!projectId || !auth) return;
    void refreshProject(projectId, true);
  }, [projectId, auth, refreshProject]);
  useEffect(() => {
    if (!projectId || !auth) return;
    const timer = setInterval(
      () => {
        if (polling.current || document.visibilityState === "hidden") return;
        polling.current = true;
        void refreshProject(projectId).finally(() => {
          polling.current = false;
        });
      },
      activeJobs.length ? 2000 : 10000,
    );
    return () => clearInterval(timer);
  }, [projectId, auth, activeJobs.length, refreshProject]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current || uploadBusy) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [uploadBusy]);
  useEffect(() => {
    selectedAssetRef.current = selectedAssetId;
    searchSequence.current += 1;
    setHits([]);
    setSearchDone(false);
    setSearchWarnings([]);
    setSearchBusy(false);
    setTranscript(null);
    setSourceTime(0);
    setMediaDuration(0);
    setMediaError("");
    setPlaying(false);
  }, [selectedAssetId, asset?.run_id]);
  useEffect(() => {
    if (!selectedAssetId || evidenceTab !== "transcript") return;
    const controller = new AbortController();
    setTranscriptBusy(true);
    api<Transcript>(`/assets/${selectedAssetId}/transcript`, {
      signal: controller.signal,
    })
      .then(setTranscript)
      .catch((err) => {
        if (
          !controller.signal.aborted &&
          !(err instanceof ApiError && [404, 409].includes(err.status))
        )
          setError(errorText(err));
      })
      .finally(() => {
        if (!controller.signal.aborted) setTranscriptBusy(false);
      });
    return () => controller.abort();
  }, [selectedAssetId, evidenceTab, asset?.status, asset?.run_id]);

  function switchProject(id: string | null) {
    if (id === projectId) return;
    if (
      (dirtyRef.current || uploadBusy) &&
      !window.confirm(
        uploadBusy
          ? "Leave this project? The upload will pause and unsaved timeline changes will be discarded."
          : "Leave this project and discard your unsaved timeline changes?",
      )
    )
      return;
    projectIdRef.current = id;
    setProjectId(id);
    setProject(null);
    setSelectedAssetId(null);
    putEditor(emptyTimeline(), true);
    setUndoHistory([]);
    setConflict(false);
    setError("");
    setDialog(null);
    setSidebarOpen(false);
    previewQueue.current = [];
    setPreviewing(false);
  }
  function selectAsset(id: string) {
    previewQueue.current = [];
    setPreviewing(false);
    pendingSeek.current = null;
    setSelectedAssetId(id);
    setAssetMenu(null);
  }
  async function saveEditor() {
    if (!projectId || saving || !canEdit) return;
    const snapshot = structuredClone(editorRef.current);
    const invalid = validateTimeline(snapshot, sourceAsset?.duration);
    if (invalid) {
      setError(invalid);
      return;
    }
    const revision = editRevision.current;
    setSaving(true);
    setError("");
    try {
      const result = await api<Timeline>(
        `/projects/${projectId}/timeline`,
        json("PUT", {
          version: snapshot.version,
          asset_id: snapshot.asset_id,
          run_id: snapshot.run_id,
          clips: snapshot.clips,
        }),
      );
      if (projectIdRef.current !== projectId) return;
      baselineRef.current = timelineSignature(result);
      if (editRevision.current === revision) putEditor(result, true);
      else putEditor({ ...editorRef.current, version: result.version });
      setConflict(false);
      notify(`Version ${result.version} saved.`);
      void refreshProject(projectId);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConflict(true);
        showDialog("conflict");
        void refreshProject(projectId);
      } else setError(errorText(err));
    } finally {
      setSaving(false);
    }
  }
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || dialog || !project) return;
      const target = event.target as HTMLElement;
      if (event.key.toLowerCase() === "s") {
        event.preventDefault();
        if (dirty && canEdit) void saveEditor();
      }
      if (
        event.key.toLowerCase() === "z" &&
        !["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) &&
        !target.isContentEditable
      ) {
        event.preventDefault();
        undo();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  });
  async function search() {
    if (!asset || !query.trim() || searchBusy) return;
    const token = ++searchSequence.current;
    setSearchBusy(true);
    setError("");
    try {
      const result = await api<{ hits: Hit[]; warnings: string[] }>(
        `/assets/${asset.id}/search`,
        json("POST", { query: query.trim(), mode, limit: 8 }),
      );
      if (token !== searchSequence.current) return;
      setHits(result.hits);
      setSearchWarnings(result.warnings ?? []);
      setSearchDone(true);
    } catch (err) {
      if (token === searchSequence.current) setError(errorText(err));
    } finally {
      if (token === searchSequence.current) setSearchBusy(false);
    }
  }
  async function generatePlan() {
    if (!asset || !query.trim() || planBusy) return;
    const id = asset.id;
    setPlanBusy(true);
    setError("");
    try {
      const result = await api<Plan>(
        `/assets/${id}/plan`,
        json("POST", {
          query: query.trim(),
          mode,
          target_seconds: targetSeconds,
        }),
      );
      if (selectedAssetRef.current !== id) return;
      setDraft(result);
      showDialog("plan");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPlanBusy(false);
    }
  }
  function applyPlan() {
    if (!draft || !draft.clips.length || !canEdit) return;
    if (
      editor.clips.length &&
      !window.confirm(
        "Replace the current timeline with this draft? You can undo this change before leaving the project.",
      )
    )
      return;
    edit({
      ...editorRef.current,
      asset_id: draft.asset_id,
      run_id: draft.run_id,
      clips: draft.clips,
    });
    setSelectedAssetId(draft.asset_id);
    setDialog(null);
    notify(
      "Draft added to your timeline. Review the cuts, then save a version.",
    );
  }
  function addClip(hit?: Hit) {
    if (!asset || !canEdit || !ready) {
      setError(
        "Wait for this recording to finish analysis before adding clips.",
      );
      return;
    }
    if (editor.clips.length >= 30) {
      setError("A timeline supports up to 30 clips.");
      return;
    }
    const isDifferentSource =
      !!editor.asset_id &&
      editor.asset_id !== asset.id &&
      editor.clips.length > 0;
    if (
      isDifferentSource &&
      !window.confirm(
        "This version uses a different recording. Start a new timeline with the selected source? You can undo this change.",
      )
    )
      return;
    const start = hit ? hit.start : Math.max(0, sourceTime);
    const end = hit
      ? hit.end
      : Math.min(start + 15, asset.duration ?? mediaDuration ?? start + 15);
    if (end <= start) {
      setError(
        "Move the playhead before the end of the recording to add a clip.",
      );
      return;
    }
    const clip: Clip = {
      id: crypto.randomUUID().replaceAll("-", ""),
      start,
      end,
      title: hit?.text.slice(0, 80) || `Moment at ${timecode(start)}`,
      subtitle: "",
      evidence_ids: hit ? [hit.id] : [],
    };
    edit({
      ...editorRef.current,
      asset_id: asset.id,
      run_id: asset.run_id,
      clips: [...(isDifferentSource ? [] : editorRef.current.clips), clip],
    });
    setSelectedClip(clip.id);
    notify("Moment added to your cut.");
  }
  function seek(time: number, end?: number) {
    previewQueue.current = end
      ? [
          {
            id: "evidence-preview",
            start: time,
            end,
            title: "",
            subtitle: "",
            evidence_ids: [],
          },
        ]
      : [];
    setPreviewing(false);
    if (video.current && video.current.readyState >= 1) {
      video.current.currentTime = time;
      void video.current.play().catch(() => {});
    } else pendingSeek.current = { time, play: true };
  }
  function preview(id?: string) {
    if (!sourceAsset) {
      setError(
        "The source recording for this edit was removed. Add a clip from an available recording to start a new cut.",
      );
      return;
    }
    const clips = id
      ? editor.clips.filter((clip) => clip.id === id)
      : editor.clips;
    if (!clips.length || !editor.asset_id) return;
    previewQueue.current = [...clips];
    setPreviewing(true);
    setSelectedClip(clips[0].id);
    if (selectedAssetId !== editor.asset_id) {
      pendingSeek.current = { time: clips[0].start, play: true };
      setSelectedAssetId(editor.asset_id);
    } else if (video.current) {
      video.current.currentTime = clips[0].start;
      void video.current.play().catch(() => {});
    }
  }
  function onTimeUpdate() {
    const player = video.current;
    if (!player) return;
    setSourceTime(player.currentTime);
    const current = previewQueue.current[0];
    if (current && player.currentTime >= current.end - 0.04) {
      previewQueue.current.shift();
      const next = previewQueue.current[0];
      if (next) {
        player.currentTime = next.start;
        setSelectedClip(next.id);
        void player.play().catch(() => {});
      } else {
        player.pause();
        setPreviewing(false);
      }
    }
  }
  function togglePlay() {
    if (!video.current) return;
    if (video.current.paused)
      void video.current.play().catch((err) => setMediaError(errorText(err)));
    else video.current.pause();
  }
  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setDialogBusy(true);
    setDialogError("");
    const data = new FormData(event.currentTarget);
    try {
      const result = await api<ProjectSummary>(
        "/projects",
        json("POST", { name: String(data.get("name")).trim() }),
      );
      setProjects((items) => [result, ...items]);
      switchProject(result.id);
      setDialog(null);
    } catch (err) {
      setDialogError(errorText(err));
    } finally {
      setDialogBusy(false);
    }
  }
  async function mutate(
    path: string,
    method: string,
    body?: unknown,
    message?: string,
  ) {
    setDialogBusy(true);
    setDialogError("");
    setError("");
    try {
      await api(path, json(method, body));
      if (projectId) await refreshProject(projectId);
      if (message) notify(message);
      return true;
    } catch (err) {
      if (dialog) setDialogError(errorText(err));
      else setError(errorText(err));
      return false;
    } finally {
      setDialogBusy(false);
    }
  }
  async function deleteAsset(item: Asset) {
    if (
      !window.confirm(
        `Delete “${item.name}”? Its media, analysis, and access will be removed. This cannot be undone.`,
      )
    )
      return;
    if (
      dirtyRef.current &&
      editorRef.current.asset_id === item.id &&
      !window.confirm(
        "Your unsaved edit uses this recording and will no longer be playable. Discard that edit and delete the recording?",
      )
    )
      return;
    const success = await mutate(
      `/assets/${item.id}`,
      "DELETE",
      undefined,
      "Recording deleted.",
    );
    if (success && editorRef.current.asset_id === item.id) {
      putEditor(emptyTimeline(), true);
      setUndoHistory([]);
      if (projectId) void refreshProject(projectId);
    }
    setAssetMenu(null);
  }
  async function exportTimeline() {
    if (!projectId || dirty || !editor.clips.length || !sourceAsset) return;
    setDialogBusy(true);
    setDialogError("");
    try {
      await api<Export>(
        `/projects/${projectId}/exports`,
        json("POST", { version: editor.version }),
      );
      await refreshProject(projectId);
      notify("Export queued. Your saved version is frozen for this render.");
    } catch (err) {
      setDialogError(errorText(err));
    } finally {
      setDialogBusy(false);
    }
  }
  async function openHistory() {
    if (!projectId) return;
    showDialog("history");
    setDialogBusy(true);
    try {
      setVersions(
        await api<Timeline[]>(`/projects/${projectId}/timeline/versions`),
      );
    } catch (err) {
      setDialogError(errorText(err));
    } finally {
      setDialogBusy(false);
    }
  }
  async function restoreVersion(version: number) {
    if (!projectId || !canEdit) return;
    if (
      !window.confirm(
        dirty
          ? "Restore this saved version and discard your unsaved local changes?"
          : "Restore this snapshot as a new version? Your previous versions remain available.",
      )
    )
      return;
    setDialogBusy(true);
    setDialogError("");
    try {
      const restored = await api<Timeline>(
        `/projects/${projectId}/timeline/restore`,
        json("POST", {
          version: project?.timeline.version ?? editor.version,
          target_version: version,
        }),
      );
      putEditor(restored, true);
      setUndoHistory([]);
      setConflict(false);
      setDialog(null);
      notify(`Restored as version ${restored.version}.`);
      void refreshProject(projectId);
    } catch (err) {
      setDialogError(errorText(err));
      if (err instanceof ApiError && err.status === 409)
        void refreshProject(projectId);
    } finally {
      setDialogBusy(false);
    }
  }
  function downloadLocalEdit() {
    const blob = new Blob([JSON.stringify(editorRef.current, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "replay-unsaved-timeline.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function logout() {
    if (
      (dirty || uploadBusy) &&
      !window.confirm(
        "Sign out? Your upload will pause and unsaved timeline changes will be discarded.",
      )
    )
      return;
    try {
      await api("/auth/logout", json("POST"));
      setAuth(null);
      setCsrf("");
      projectIdRef.current = null;
      setProjectId(null);
      setProject(null);
      setProjects([]);
      putEditor(emptyTimeline(), true);
    } catch (err) {
      setError(errorText(err));
    }
  }

  if (booting)
    return (
      <div className="boot">
        <Brand />
        <Spinner label="Opening your studio…" />
      </div>
    );
  if (bootError)
    return (
      <div className="boot">
        <Brand />
        <Notice warning>{bootError}</Notice>
        <button className="button" onClick={() => void bootstrap()}>
          <RefreshCw size={15} />
          Reconnect
        </button>
        <p className="muted">
          The studio API must be running on this origin, or behind the
          development proxy.
        </p>
      </div>
    );
  if (!auth)
    return (
      <AuthScreen
        setup={authStatus.needs_setup}
        registrationOpen={authStatus.registration_open}
        onAuth={(value) => {
          if (lastUserId.current && lastUserId.current !== value.user.id) {
            projectIdRef.current = null;
            setProjectId(null);
            setProject(null);
            setSelectedAssetId(null);
            putEditor(emptyTimeline(), true);
            setUndoHistory([]);
            setConflict(false);
          }
          lastUserId.current = value.user.id;
          setCsrf(value.csrf_token);
          setAuth(value);
        }}
      />
    );

  return (
    <div className={`studio-shell ${sidebarOpen ? "sidebar-open" : ""}`}>
      <aside className="sidebar">
        <div className="sidebar-brand">
          <Brand />
          <button
            className="icon-button mobile-close"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close navigation"
          >
            <X size={18} />
          </button>
        </div>
        <div className="workspace-label">
          <span className="workspace-avatar">
            {auth.user.name?.charAt(0).toUpperCase() || "R"}
          </span>
          <div>
            {auth.user.name || "My workspace"}
            <small>Personal workspace</small>
          </div>
          <ChevronDown size={14} />
        </div>
        <nav className="main-nav" aria-label="Workspace">
          <button
            className={!projectId ? "active" : ""}
            onClick={() => switchProject(null)}
          >
            <LayoutGrid size={16} />
            All projects<span>{projects.length}</span>
          </button>
        </nav>
        <div className="sidebar-section-title">
          <span>PROJECTS</span>
          <button
            className="icon-button tiny"
            onClick={() => showDialog("new")}
            aria-label="Create a project"
          >
            <Plus size={15} />
          </button>
        </div>
        <div className="project-nav">
          {projectsLoading && !projects.length ? (
            <Spinner label="Loading projects" />
          ) : (
            projects.map((item) => (
              <button
                className={projectId === item.id ? "active" : ""}
                key={item.id}
                onClick={() => switchProject(item.id)}
              >
                <FolderOpen size={14} />
                <span>{item.name}</span>
                {projectId === item.id && <span className="selected-dot" />}
              </button>
            ))
          )}
          {!projectsLoading && !projects.length && (
            <p className="sidebar-empty">Your first project starts here.</p>
          )}
        </div>
        {project && (
          <div className="media-library">
            <div className="sidebar-section-title">
              <span>MEDIA LIBRARY</span>
              <span>{project.assets.length}</span>
            </div>
            {canEdit && (
              <Uploader
                key={project.id}
                projectId={project.id}
                onComplete={() => void refreshProject(project.id)}
                onBusy={setUploadBusy}
              />
            )}
            <div className="asset-list">
              {project.assets.map((item) => (
                <div
                  className={`asset-item ${selectedAssetId === item.id ? "active" : ""}`}
                  key={item.id}
                >
                  <button
                    className="asset-select"
                    onClick={() => selectAsset(item.id)}
                  >
                    <span className="asset-thumbnail">
                      {doneStatus(item.status) ? (
                        <img
                          src={frameUrl(item.id, "frame_000001.jpg")}
                          alt=""
                          loading="lazy"
                          onError={(event) => {
                            event.currentTarget.style.display = "none";
                          }}
                        />
                      ) : (
                        <Film size={19} strokeWidth={1.3} />
                      )}
                    </span>
                    <span>
                      <strong>{item.name}</strong>
                      <small>
                        {item.duration
                          ? timecode(item.duration)
                          : bytes(item.size)}
                        <span>·</span>
                        {item.status.replaceAll("_", " ")}
                      </small>
                    </span>
                  </button>
                  {canEdit && (
                    <button
                      className="icon-button tiny asset-more"
                      aria-label={`Options for ${item.name}`}
                      onClick={() =>
                        setAssetMenu(assetMenu === item.id ? null : item.id)
                      }
                    >
                      <Ellipsis size={16} />
                    </button>
                  )}
                  {assetMenu === item.id && (
                    <div className="asset-menu">
                      <button
                        onClick={() => {
                          selectAsset(item.id);
                          showDialog("analyze");
                        }}
                      >
                        <RefreshCw size={13} />
                        Reanalyze
                      </button>
                      <button
                        className="danger"
                        onClick={() => void deleteAsset(item)}
                      >
                        <Trash2 size={13} />
                        Delete recording
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
            {project.jobs.length > 0 && (
              <details className="pipeline-status" open={activeJobs.length > 0}>
                <summary>
                  <span
                    className={`status-dot ${activeJobs.length ? "working" : ""}`}
                  />
                  Processing activity
                  <span>
                    {activeJobs.length ? `${activeJobs.length} active` : "View"}
                  </span>
                </summary>
                <div className="job-list">
                  {project.jobs.slice(-15).map((job) => (
                    <div className="job-row" key={job.id}>
                      <div>
                        <span>{job.kind.replaceAll("_", " ")}</span>
                        <Status value={job.status} />
                      </div>
                      {job.error && <p>{job.error}</p>}
                      {canEdit && (
                        <div className="job-actions">
                          {activeStatus(job.status) && (
                            <button
                              className="text-button"
                              disabled={dialogBusy}
                              onClick={() =>
                                void mutate(
                                  `/jobs/${job.id}/cancel`,
                                  "POST",
                                  undefined,
                                  "Cancellation requested.",
                                )
                              }
                            >
                              <X size={10} />
                              Cancel
                            </button>
                          )}
                          {["failed", "cancelled", "blocked"].includes(
                            job.status,
                          ) && (
                            <button
                              className="text-button"
                              disabled={dialogBusy}
                              onClick={() =>
                                void mutate(
                                  `/jobs/${job.id}/retry`,
                                  "POST",
                                  undefined,
                                  "Retry requested.",
                                )
                              }
                            >
                              <RotateCcw size={10} />
                              Retry
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
        <div className="sidebar-bottom">
          <button onClick={() => showDialog("capabilities")}>
            <Settings2 size={15} />
            System capabilities
            <CircleHelp size={13} />
          </button>
          <div className="account">
            <span className="account-avatar">
              {auth.user.name?.slice(0, 2).toUpperCase() || "ME"}
            </span>
            <span>
              {auth.user.name}
              <small>{auth.user.email}</small>
            </span>
            <button
              className="icon-button"
              aria-label="Sign out"
              title="Sign out"
              onClick={() => void logout()}
            >
              <LogOut size={15} />
            </button>
          </div>
        </div>
      </aside>
      <main className="workspace">
        <header className="topbar">
          <div className="breadcrumbs">
            <button
              className="icon-button mobile-nav"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open navigation"
            >
              <LayoutGrid size={18} />
            </button>
            <button onClick={() => switchProject(null)}>Workspace</button>
            {project && (
              <>
                <ChevronRight size={13} />
                <span>{project.name}</span>
                <span className="project-role">{project.role}</span>
              </>
            )}
          </div>
          <div className="topbar-actions">
            {project && (
              <>
                <button
                  className="button ghost small"
                  onClick={() => showDialog("comments")}
                >
                  <MessageSquare size={15} />
                  <span>Comments</span>
                  {project.comments.length > 0 && (
                    <span className="comment-count">
                      {project.comments.length}
                    </span>
                  )}
                </button>
                <button
                  className="button ghost small"
                  onClick={() => showDialog("team")}
                >
                  <Users size={15} />
                  <span>Share</span>
                </button>
                <span className="topbar-divider" />
                <button
                  className="button primary small"
                  onClick={() => showDialog("exports")}
                >
                  <ArrowDownToLine size={14} />
                  Export
                </button>
              </>
            )}
          </div>
        </header>
        {error && (
          <div className="global-error" role="alert">
            <Notice warning>{error}</Notice>
            <button
              className="icon-button"
              onClick={() => setError("")}
              aria-label="Dismiss error"
            >
              <X size={16} />
            </button>
          </div>
        )}
        {!projectId ? (
          <div className="projects-page">
            <div className="page-heading">
              <div>
                <span className="eyebrow">YOUR WORKSPACE</span>
                <h1>
                  A little less footage.
                  <br />
                  <span>A lot more story.</span>
                </h1>
                <p>Bring your recordings. Find the moments worth keeping.</p>
              </div>
              <button
                className="button primary"
                onClick={() => showDialog("new")}
              >
                <Plus size={17} />
                New project
              </button>
            </div>
            <div className="project-grid-heading">
              <h2>
                All projects <span>{projects.length}</span>
              </h2>
              <span>Recently updated</span>
            </div>
            <div className="project-grid">
              {projects.map((item, index) => (
                <button
                  className="project-card"
                  key={item.id}
                  onClick={() => switchProject(item.id)}
                >
                  <div className={`project-card-art art-${index % 4}`}>
                    <div className="project-film-lines" aria-hidden="true">
                      <span />
                      <span />
                      <span />
                    </div>
                    <span className="project-art-icon">
                      <MonitorPlay size={36} strokeWidth={1.2} />
                    </span>
                    <span className="project-art-label">
                      REPLAY / {String(index + 1).padStart(2, "0")}
                    </span>
                    <ArrowRight size={17} />
                  </div>
                  <div className="project-card-info">
                    <h3>{item.name}</h3>
                    <span>
                      {item.role}
                      <span>·</span>Updated {dateLabel(item.updated_at)}
                    </span>
                  </div>
                </button>
              ))}
              <button
                className="new-project-card"
                onClick={() => showDialog("new")}
              >
                <span>
                  <Plus size={26} strokeWidth={1.3} />
                </span>
                <strong>Create a project</strong>
                <small>A fresh cut starts here.</small>
              </button>
            </div>
            {projectsLoading && <Spinner label="Loading projects…" />}
            <div className="workspace-guide">
              <span className="eyebrow">FROM RECORDING TO REPLAY</span>
              <div>
                <span>01</span>
                <p>
                  <strong>Bring your source</strong>Upload a talk, walkthrough,
                  or demo you have permission to use.
                </p>
              </div>
              <div>
                <span>02</span>
                <p>
                  <strong>Find the evidence</strong>Search speech, text on
                  screen, and visual moments together.
                </p>
              </div>
              <div>
                <span>03</span>
                <p>
                  <strong>Make your cut</strong>Refine the timeline and export
                  with captions and provenance.
                </p>
              </div>
            </div>
          </div>
        ) : loadingProject && !project ? (
          <div className="loading-project">
            <Spinner label="Opening project…" />
          </div>
        ) : project ? (
          <div className="editor-page">
            <div className="editor-heading">
              <div>
                <span className="eyebrow">EDIT ROOM</span>
                <h1>{project.name}</h1>
              </div>
              <div>
                <span className="local-pill">
                  <span />
                  {activeJobs.length
                    ? "Processing media"
                    : dirty
                      ? "Local changes"
                      : "Workspace synced"}
                </span>
                <button
                  className="button ghost small"
                  onClick={() => void openHistory()}
                >
                  <History size={14} />
                  Version history
                </button>
              </div>
            </div>
            <div className="editing-grid">
              <section className="viewer-panel" aria-label="Source video">
                <div className="viewer-heading">
                  <div className="panel-title">
                    <MonitorPlay size={15} />
                    <h2>Source monitor</h2>
                  </div>
                  <span className="source-filename" title={asset?.name}>
                    {asset?.name ?? "No recording selected"}
                  </span>
                  {asset && <Status value={asset.status} />}
                </div>
                <div className={`video-stage ${!asset ? "no-source" : ""}`}>
                  {asset ? (
                    <>
                      <video
                        key={`${asset.id}-${asset.run_id}-${ready ? "proxy" : "source"}`}
                        ref={video}
                        src={
                          mediaUrl(asset.id) +
                          `?run=${encodeURIComponent(asset.run_id ?? "")}&ready=${ready}`
                        }
                        preload="metadata"
                        poster={
                          ready
                            ? frameUrl(asset.id, "frame_000001.jpg") +
                              `?run=${encodeURIComponent(asset.run_id ?? "")}`
                            : undefined
                        }
                        playsInline
                        onClick={togglePlay}
                        onTimeUpdate={onTimeUpdate}
                        onPlay={() => setPlaying(true)}
                        onPause={() => setPlaying(false)}
                        onEnded={() => {
                          setPlaying(false);
                          setPreviewing(false);
                        }}
                        onLoadedMetadata={() => {
                          setMediaError("");
                          setMediaDuration(video.current?.duration ?? 0);
                          if (pendingSeek.current && video.current) {
                            video.current.currentTime =
                              pendingSeek.current.time;
                            if (pendingSeek.current.play)
                              void video.current.play().catch(() => {});
                            pendingSeek.current = null;
                          }
                        }}
                        onError={() =>
                          setMediaError(
                            ready
                              ? "The video could not be played. Try refreshing, or check the processing activity."
                              : "The browser cannot play this source yet. The normalized preview will appear after preparation.",
                          )
                        }
                      />
                      {previewing && (
                        <span className="preview-badge">
                          <Play size={11} />
                          PLAYING YOUR CUT
                          <button
                            onClick={() => {
                              previewQueue.current = [];
                              setPreviewing(false);
                              video.current?.pause();
                            }}
                            aria-label="Stop timeline preview"
                          >
                            <X size={12} />
                          </button>
                        </span>
                      )}
                      {!playing && !mediaError && (
                        <button
                          className="stage-play"
                          aria-label="Play source video"
                          onClick={togglePlay}
                        >
                          <Play size={23} fill="currentColor" />
                        </button>
                      )}
                      {mediaError && (
                        <div className="media-error">
                          <Film size={25} />
                          <p>{mediaError}</p>
                          <button
                            className="button small"
                            onClick={() => {
                              setMediaError("");
                              video.current?.load();
                            }}
                          >
                            <RefreshCw size={13} />
                            Retry preview
                          </button>
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="video-empty">
                      <div className="empty-screen-art" aria-hidden="true">
                        <span />
                        <Film size={40} strokeWidth={1} />
                        <span />
                      </div>
                      <h2>Your story starts with a source.</h2>
                      <p>
                        {canEdit
                          ? "Upload a recording from the media library to enter the edit."
                          : "A project editor can upload the first recording."}
                      </p>
                      <span>VIDEO · AUDIO · EVIDENCE</span>
                    </div>
                  )}
                </div>
                <div className="player-controls">
                  <div className="source-scrubber">
                    <label
                      className="visually-hidden"
                      htmlFor="source-position"
                    >
                      Source playback position
                    </label>
                    <input
                      id="source-position"
                      type="range"
                      min={0}
                      max={
                        Number.isFinite(mediaDuration) ? mediaDuration || 1 : 1
                      }
                      step={0.05}
                      value={Math.min(sourceTime, mediaDuration || 1)}
                      disabled={!asset || !mediaDuration}
                      onChange={(event) => {
                        const value = Number(event.currentTarget.value);
                        previewQueue.current = [];
                        setPreviewing(false);
                        setSourceTime(value);
                        if (video.current) video.current.currentTime = value;
                      }}
                    />
                  </div>
                  <div className="transport">
                    <div>
                      <button
                        className="icon-button"
                        disabled={!asset}
                        onClick={() => {
                          previewQueue.current = [];
                          setPreviewing(false);
                          if (video.current) video.current.currentTime = 0;
                        }}
                        aria-label="Go to beginning"
                      >
                        <SkipBack size={15} />
                      </button>
                      <button
                        className="transport-play"
                        disabled={!asset}
                        onClick={togglePlay}
                        aria-label={playing ? "Pause video" : "Play video"}
                      >
                        {playing ? (
                          <Pause size={15} fill="currentColor" />
                        ) : (
                          <Play size={15} fill="currentColor" />
                        )}
                      </button>
                      <span className="player-time">
                        {timecode(sourceTime, true)}
                        <span>
                          / {timecode(mediaDuration || asset?.duration)}
                        </span>
                      </span>
                    </div>
                    <div>
                      <span className="source-label">SOURCE TIMELINE</span>
                      <label className="playback-speed">
                        <select
                          aria-label="Playback speed"
                          defaultValue="1"
                          onChange={(event) => {
                            if (video.current)
                              video.current.playbackRate = Number(
                                event.currentTarget.value,
                              );
                          }}
                        >
                          <option value="0.75">0.75×</option>
                          <option value="1">1×</option>
                          <option value="1.25">1.25×</option>
                          <option value="1.5">1.5×</option>
                          <option value="2">2×</option>
                        </select>
                      </label>
                      <button
                        className="icon-button"
                        disabled={!asset}
                        title="Use browser video controls for volume"
                        aria-label="Show native video controls"
                        onClick={() => {
                          if (video.current)
                            video.current.controls = !video.current.controls;
                        }}
                      >
                        <Volume2 size={16} />
                      </button>
                    </div>
                  </div>
                </div>
                <div className="viewer-bottom">
                  <span>
                    <Shield size={12} />
                    Original source preserved
                  </span>
                  <button
                    className="text-button"
                    disabled={!canEdit || !ready}
                    onClick={() => addClip()}
                  >
                    <Plus size={13} />
                    Add 15s at playhead
                  </button>
                </div>
                <div className="draft-bar">
                  <span className="draft-icon">
                    <Sparkles size={19} />
                  </span>
                  <div>
                    <strong>A first cut, ready for your eye.</strong>
                    <p>
                      Use your search to assemble a draft. Review every moment
                      before exporting.
                    </p>
                  </div>
                  <button
                    className="button small"
                    disabled={!ready || !query.trim() || !canEdit || planBusy}
                    onClick={() => void generatePlan()}
                  >
                    {planBusy ? (
                      <Spinner label="Drafting" />
                    ) : (
                      <>
                        Draft edit
                        <ArrowRight size={13} />
                      </>
                    )}
                  </button>
                </div>
              </section>
              <EvidencePanel
                assetId={asset?.id}
                query={query}
                setQuery={setQuery}
                mode={mode}
                setMode={setMode}
                onSearch={() => void search()}
                hits={hits}
                searchBusy={searchBusy}
                searchDone={searchDone}
                searchWarnings={searchWarnings}
                onSeek={seek}
                onAdd={addClip}
                canEdit={canEdit}
                onPlan={() => void generatePlan()}
                planBusy={planBusy}
                transcript={transcript}
                transcriptBusy={transcriptBusy}
                tab={evidenceTab}
                setTab={setEvidenceTab}
                ready={ready}
              />
            </div>
            {editor.asset_id && !sourceAsset && (
              <div className="removed-source">
                <Notice warning>
                  The source for this timeline was removed. Its saved versions
                  remain in history; add a moment from another recording to
                  start a new cut.
                </Notice>
              </div>
            )}
            <TimelineEditor
              timeline={editor}
              selected={selectedClip}
              onSelect={setSelectedClip}
              onChange={edit}
              onUndo={undo}
              canUndo={undoHistory.length > 0}
              onSave={() => void saveEditor()}
              saving={saving}
              dirty={dirty}
              canEdit={canEdit}
              onPreview={preview}
              onAdd={() => addClip()}
              sourceName={sourceAsset?.name}
              sourceDuration={sourceAsset?.duration}
              conflict={conflict}
              onResolve={() => showDialog("conflict")}
            />
            <footer className="editor-footer">
              <span>
                <Shield size={11} />
                Evidence stays attached to your edit.
              </span>
              <span>
                Alt + ← / → to reorder clips <span>·</span> Ctrl / ⌘ + S to save
              </span>
            </footer>
          </div>
        ) : (
          <Empty
            title="Project unavailable"
            detail="Check your access or return to your workspace."
            children={
              <button className="button" onClick={() => switchProject(null)}>
                Back to projects
              </button>
            }
          />
        )}
      </main>
      {toast && (
        <div className="toast" role="status">
          <Check size={16} />
          {toast}
          <button
            aria-label="Dismiss notification"
            onClick={() => setToast("")}
          >
            <X size={14} />
          </button>
        </div>
      )}
      {dialog && (
        <Modal
          title={
            dialog === "new"
              ? "A new place to create."
              : dialog === "team"
                ? "Your project, your people."
                : dialog === "comments"
                  ? "Keep the conversation in context."
                  : dialog === "exports"
                    ? "Ready for a replay."
                    : dialog === "history"
                      ? "Every version, kept."
                      : dialog === "capabilities"
                        ? "What your studio can do."
                        : dialog === "analyze"
                          ? "Reanalyze this recording."
                          : dialog === "conflict"
                            ? "Two edits. Nothing lost."
                            : "Your first cut."
          }
          eyebrow={
            dialog === "new"
              ? "CREATE PROJECT"
              : dialog === "team"
                ? "PROJECT ACCESS"
                : dialog === "comments"
                  ? "PROJECT COMMENTS"
                  : dialog === "exports"
                    ? "EXPORT CENTER"
                    : dialog === "history"
                      ? "VERSION HISTORY"
                      : dialog === "capabilities"
                        ? "SYSTEM CAPABILITIES"
                        : dialog === "analyze"
                          ? "ANALYSIS SETTINGS"
                          : dialog === "conflict"
                            ? "VERSION CONFLICT"
                            : "DRAFT PREVIEW"
          }
          onClose={() => {
            if (!dialogBusy) setDialog(null);
          }}
          wide={["plan", "exports", "comments"].includes(dialog)}
        >
          {dialogError && <Notice warning>{dialogError}</Notice>}
          {dialog === "new" && (
            <form onSubmit={createProject} className="modal-form">
              <p>
                Give your recording a home. You can invite collaborators once
                the project is created.
              </p>
              <label>
                Project name
                <input
                  name="name"
                  required
                  maxLength={120}
                  placeholder="e.g. Product walkthrough — September"
                  autoFocus
                />
              </label>
              <div className="modal-actions">
                <button
                  type="button"
                  className="button"
                  onClick={() => setDialog(null)}
                >
                  Cancel
                </button>
                <button className="button primary" disabled={dialogBusy}>
                  {dialogBusy ? (
                    <Spinner label="Creating" />
                  ) : (
                    <>
                      Create project
                      <ArrowRight size={15} />
                    </>
                  )}
                </button>
              </div>
            </form>
          )}
          {dialog === "team" && project && (
            <>
              <p className="modal-intro">
                Owners manage access. Editors upload and edit. Viewers can
                watch, review, and comment.
              </p>
              <div className="members-list">
                {project.members.map((member) => (
                  <div
                    className="member-row"
                    key={member.user_id ?? member.id ?? member.email}
                  >
                    <span className="member-avatar">
                      {(member.name ?? member.email).slice(0, 2).toUpperCase()}
                    </span>
                    <div>
                      <strong>{member.name || member.email}</strong>
                      <small>{member.email}</small>
                    </div>
                    <span className="role-badge">{member.role}</span>
                    {owner && member.role !== "owner" && (
                      <button
                        className="icon-button danger"
                        disabled={dialogBusy}
                        aria-label={`Remove ${member.email}`}
                        onClick={() => {
                          if (
                            window.confirm(
                              `Remove ${member.email} from this project?`,
                            )
                          )
                            void mutate(
                              `/projects/${project.id}/members/${member.user_id ?? member.id}`,
                              "DELETE",
                              undefined,
                              "Project access removed.",
                            );
                        }}
                      >
                        <X size={14} />
                      </button>
                    )}
                  </div>
                ))}
              </div>
              {owner ? (
                <form
                  className="invite-form"
                  onSubmit={(event) => {
                    event.preventDefault();
                    const form = event.currentTarget;
                    const data = new FormData(form);
                    void mutate(
                      `/projects/${project.id}/members`,
                      "POST",
                      { email: data.get("email"), role: data.get("role") },
                      "Project access updated.",
                    ).then((ok) => {
                      if (ok) form.reset();
                    });
                  }}
                >
                  <label>
                    Email address
                    <input
                      name="email"
                      type="email"
                      required
                      placeholder="teammate@example.com"
                    />
                  </label>
                  <label>
                    Role
                    <select name="role" defaultValue="editor">
                      <option value="editor">Editor</option>
                      <option value="viewer">Viewer</option>
                    </select>
                  </label>
                  <button className="button primary" disabled={dialogBusy}>
                    <Plus size={14} />
                    Add member
                  </button>
                  <p>
                    Members need an existing studio account. This grants access;
                    it does not send an email.
                  </p>
                </form>
              ) : (
                <Notice>Only the project owner can change access.</Notice>
              )}
            </>
          )}
          {dialog === "comments" && project && (
            <>
              <div className="comments-list">
                {project.comments.length ? (
                  project.comments.map((comment) => (
                    <article key={comment.id} className="comment">
                      <div className="comment-head">
                        <span className="member-avatar">
                          {(
                            comment.author?.name ??
                            comment.user?.name ??
                            comment.author_name ??
                            comment.name ??
                            "M"
                          )
                            .slice(0, 2)
                            .toUpperCase()}
                        </span>
                        <strong>
                          {comment.author?.name ??
                            comment.user?.name ??
                            comment.author_name ??
                            comment.name ??
                            "Project member"}
                        </strong>
                        <span>{dateLabel(comment.created_at)}</span>
                        {comment.time != null && (
                          <button
                            className="text-button"
                            disabled={
                              comment.asset_id
                                ? !project.assets.some(
                                    (item) => item.id === comment.asset_id,
                                  )
                                : project.assets.length !== 1
                            }
                            title={
                              comment.asset_id
                                ? "Jump to the linked source recording"
                                : "Legacy timestamp: the source must be unambiguous"
                            }
                            onClick={() => {
                              setDialog(null);
                              const targetAsset =
                                comment.asset_id ?? project.assets[0]?.id;
                              if (!targetAsset) return;
                              if (targetAsset !== selectedAssetId) {
                                previewQueue.current = [];
                                setPreviewing(false);
                                pendingSeek.current = {
                                  time: comment.time!,
                                  play: true,
                                };
                                setSelectedAssetId(targetAsset);
                              } else seek(comment.time!);
                            }}
                          >
                            <Clock3 size={11} />
                            {timecode(comment.time)}
                          </button>
                        )}
                      </div>
                      <p>{comment.text}</p>
                    </article>
                  ))
                ) : (
                  <Empty
                    title="A fresh conversation"
                    detail="Share feedback and attach a source timestamp so your team can find the moment."
                  />
                )}
              </div>
              <form
                className="comment-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  const form = event.currentTarget;
                  const data = new FormData(form);
                  const text = String(data.get("text")).trim();
                  if (!text) return;
                  void mutate(
                    `/projects/${project.id}/comments`,
                    "POST",
                    {
                      text,
                      ...(data.get("timestamp") && asset
                        ? { time: sourceTime, asset_id: asset.id }
                        : {}),
                    },
                    "Comment posted.",
                  ).then((ok) => {
                    if (ok) form.reset();
                  });
                }}
              >
                <label className="visually-hidden" htmlFor="comment-text">
                  Write a comment
                </label>
                <textarea
                  id="comment-text"
                  name="text"
                  rows={3}
                  required
                  maxLength={2000}
                  placeholder="What should the team know about this moment?"
                />
                <div>
                  <label className="checkbox-label">
                    <input name="timestamp" type="checkbox" disabled={!asset} />
                    Attach current source time ({timecode(sourceTime)})
                  </label>
                  <button
                    className="button primary small"
                    disabled={dialogBusy}
                  >
                    Post comment
                    <ArrowRight size={13} />
                  </button>
                </div>
              </form>
            </>
          )}
          {dialog === "exports" && project && (
            <>
              <div className="export-summary">
                <div className="export-icon">
                  <Film size={28} strokeWidth={1.4} />
                </div>
                <div>
                  <h3>{project.name}</h3>
                  <p>
                    {editor.clips.length} clips <span>·</span>{" "}
                    {timecode(totalDuration)} <span>·</span> Version{" "}
                    {editor.version}
                  </p>
                </div>
                <span className="export-format">MP4 / H.264</span>
              </div>
              {dirty && (
                <Notice warning>
                  Save your timeline before exporting. Exports always use a
                  saved version.
                </Notice>
              )}
              {!editor.clips.length && (
                <Notice>
                  Add a moment to your timeline to create an export.
                </Notice>
              )}
              {editor.asset_id && !sourceAsset && (
                <Notice warning>
                  This edit's source recording is no longer available. Create a
                  cut from an available source before exporting.
                </Notice>
              )}
              <p className="modal-intro">
                The render keeps the original audio. Captions download as a
                separate SRT file, with a source map recording where each clip
                came from.
              </p>
              <div className="modal-actions export-actions">
                <button
                  className="button primary"
                  disabled={
                    dialogBusy ||
                    dirty ||
                    !editor.clips.length ||
                    !sourceAsset ||
                    !canEdit ||
                    !!validateTimeline(editor, sourceAsset?.duration)
                  }
                  onClick={() => void exportTimeline()}
                >
                  {dialogBusy ? (
                    <Spinner label="Queuing" />
                  ) : (
                    <>
                      <Download size={15} />
                      Render saved version
                    </>
                  )}
                </button>
              </div>
              <div className="section-divider">
                <span>EXPORT HISTORY</span>
              </div>
              <div className="export-list">
                {[...project.exports]
                  .sort((a, b) =>
                    (b.created_at ?? "").localeCompare(a.created_at ?? ""),
                  )
                  .map((item) => (
                    <article className="export-row" key={item.id}>
                      <div>
                        <strong>
                          Version {item.version ?? item.timeline_version ?? "—"}
                        </strong>
                        <small>{dateLabel(item.created_at)}</small>
                      </div>
                      <Status value={item.status} />
                      {doneStatus(item.status) ? (
                        <div className="download-links">
                          <a
                            href={`/api/exports/${item.id}/video`}
                            download
                            title="Download MP4"
                          >
                            <Film size={13} />
                            MP4
                          </a>
                          <a
                            href={`/api/exports/${item.id}/subtitles`}
                            download
                            title="Download subtitles"
                          >
                            <FileText size={13} />
                            SRT
                          </a>
                          <a
                            href={`/api/exports/${item.id}/provenance`}
                            download
                            title="Download provenance"
                          >
                            <Shield size={13} />
                            Sources
                          </a>
                        </div>
                      ) : (
                        <small>
                          {item.error ||
                            (activeStatus(item.status)
                              ? "Rendering in the background"
                              : "Check processing activity")}
                        </small>
                      )}
                    </article>
                  ))}
                {!project.exports.length && (
                  <p className="muted">Your exports will appear here.</p>
                )}
              </div>
            </>
          )}
          {dialog === "history" && (
            <>
              {dialogBusy ? (
                <Spinner label="Loading versions…" />
              ) : (
                <div className="versions-list">
                  {[...versions]
                    .sort((a, b) => b.version - a.version)
                    .map((version) => (
                      <div className="version-row" key={version.version}>
                        <span className="version-icon">
                          <History size={16} />
                        </span>
                        <div>
                          <strong>
                            Version {version.version}
                            {version.version === project?.timeline.version && (
                              <span className="current-label">Current</span>
                            )}
                          </strong>
                          <small>
                            {dateLabel(version.updated_at)}{" "}
                            {version.clips
                              ? `· ${version.clips.length} clips`
                              : ""}
                          </small>
                        </div>
                        <button
                          className="button small"
                          disabled={
                            !canEdit ||
                            version.version === project?.timeline.version
                          }
                          onClick={() => void restoreVersion(version.version)}
                        >
                          <RotateCcw size={12} />
                          Restore
                        </button>
                      </div>
                    ))}
                  {!versions.length && (
                    <Empty
                      title="Your first version is ahead"
                      detail="Save an edit to start your project history."
                    />
                  )}
                </div>
              )}
            </>
          )}
          {dialog === "capabilities" && (
            <>
              <p className="modal-intro">
                Reported by this server. Availability does not guarantee a model
                has been downloaded or that a recording has finished analysis.
              </p>
              <div className="capability-list">
                {Object.entries(capabilities).map(([key, value]) => (
                  <div key={key}>
                    <strong>{key.replaceAll("_", " ")}</strong>
                    <span>{capabilityText(value)}</span>
                  </div>
                ))}
                {!Object.keys(capabilities).length && (
                  <Notice>Capability details are unavailable.</Notice>
                )}
              </div>
              <div className="modal-actions">
                <button
                  className="button small"
                  onClick={() => {
                    void api<CapabilityMap>("/capabilities")
                      .then(setCapabilities)
                      .catch((err) => setDialogError(errorText(err)));
                  }}
                >
                  <RefreshCw size={13} />
                  Refresh status
                </button>
              </div>
            </>
          )}
          {dialog === "analyze" && asset && (
            <>
              <p className="modal-intro">
                Choose the evidence to extract from{" "}
                <strong>{asset.name}</strong>. The new analysis gets its own
                version. Your saved timeline is not silently rewritten.
              </p>
              <div className="analysis-options">
                {(
                  [
                    [
                      "asr",
                      "Speech recognition",
                      "Transcribe the spoken audio.",
                    ],
                    [
                      "ocr",
                      "Screen text",
                      "Read visible text from sampled frames.",
                    ],
                    [
                      "visual",
                      "Visual search",
                      "Index frame appearance with visual embeddings.",
                    ],
                  ] as const
                ).map(([key, title, detail]) => (
                  <label key={key}>
                    <input
                      type="checkbox"
                      checked={analysisOptions[key]}
                      onChange={(event) => {
                        const checked = event.currentTarget.checked;
                        setAnalysisOptions((options) => ({
                          ...options,
                          [key]: checked,
                        }));
                      }}
                    />
                    <span>
                      <strong>{title}</strong>
                      <small>{detail}</small>
                    </span>
                  </label>
                ))}
              </div>
              <div className="modal-actions">
                <button className="button" onClick={() => setDialog(null)}>
                  Cancel
                </button>
                <button
                  className="button primary"
                  disabled={dialogBusy || !canEdit}
                  onClick={() => {
                    void mutate(
                      `/assets/${asset.id}/analyze`,
                      "POST",
                      analysisOptions,
                      "Analysis queued.",
                    ).then((ok) => {
                      if (ok) {
                        setDialog(null);
                        setHits([]);
                        setSearchDone(false);
                      }
                    });
                  }}
                >
                  <RefreshCw size={14} />
                  Start analysis
                </button>
              </div>
            </>
          )}
          {dialog === "conflict" && project && (
            <>
              <Notice warning>
                Your edit started from version {editor.version}; the server has
                version {project.timeline.version}. Your local clips have not
                been replaced.
              </Notice>
              <p className="modal-intro">
                Download a copy of your local edit before loading the latest
                version. To deliberately save your local cut over the latest
                version, first review the difference in another saved snapshot.
              </p>
              <div className="conflict-versions">
                <div>
                  <span>YOUR LOCAL EDIT</span>
                  <strong>{editor.clips.length} clips</strong>
                  <small>{timecode(totalDuration)}</small>
                </div>
                <ArrowRight size={18} />
                <div>
                  <span>LATEST SAVED EDIT</span>
                  <strong>{project.timeline.clips.length} clips</strong>
                  <small>Version {project.timeline.version}</small>
                </div>
              </div>
              <div className="modal-actions stacked">
                <button className="button" onClick={downloadLocalEdit}>
                  <Download size={14} />
                  Download local edit JSON
                </button>
                <button
                  className="button"
                  onClick={() => {
                    if (
                      window.confirm(
                        "Discard your local changes and load the latest saved timeline? Download a local copy first if needed.",
                      )
                    ) {
                      putEditor(project.timeline, true);
                      setUndoHistory([]);
                      setConflict(false);
                      setDialog(null);
                    }
                  }}
                >
                  Load latest saved version
                </button>
                <button
                  className="button primary"
                  onClick={() => {
                    if (
                      window.confirm(
                        `Keep your local clips and base the next save on version ${project.timeline.version}? Saving will create a new version replacing the shared timeline; previous versions remain available.`,
                      )
                    ) {
                      baselineRef.current = timelineSignature(project.timeline);
                      putEditor({
                        ...editorRef.current,
                        version: project.timeline.version,
                      });
                      setConflict(false);
                      setDialog(null);
                      notify(
                        "Local edit rebased. Review and save to create a new version.",
                      );
                    }
                  }}
                >
                  <Save size={14} />
                  Keep local edit for next save
                </button>
              </div>
            </>
          )}
          {dialog === "plan" && draft && (
            <>
              <div className="draft-description">
                <span className="draft-icon">
                  <Sparkles size={22} />
                </span>
                <div>
                  <h3>A starting point, with sources.</h3>
                  <p>Review the selection before adding it to your timeline.</p>
                </div>
              </div>
              <div className="planner-meta">
                <span>
                  Strategy <strong>{draft.strategy}</strong>
                </span>
                <label>
                  Target length
                  <select
                    value={targetSeconds}
                    onChange={(event) =>
                      setTargetSeconds(Number(event.currentTarget.value))
                    }
                  >
                    <option value={60}>1 minute</option>
                    <option value={90}>90 seconds</option>
                    <option value={180}>3 minutes</option>
                    <option value={300}>5 minutes</option>
                  </select>
                </label>
                <button
                  className="button small"
                  disabled={planBusy}
                  onClick={() => void generatePlan()}
                >
                  <RefreshCw size={12} />
                  Redraft
                </button>
              </div>
              {draft.warnings.map((warning, i) => (
                <Notice warning key={i}>
                  {warning}
                </Notice>
              ))}
              <div className="draft-clips">
                {draft.clips.map((clip, index) => (
                  <div key={clip.id}>
                    <span className="clip-number">
                      {String(index + 1).padStart(2, "0")}
                    </span>
                    <div>
                      <strong>{clip.title}</strong>
                      <p>{clip.subtitle}</p>
                      <small>
                        {timecode(clip.start)} → {timecode(clip.end)} ·{" "}
                        {clip.evidence_ids?.length ?? 0} source references
                      </small>
                    </div>
                    <button
                      className="icon-button"
                      aria-label={`Preview draft clip ${index + 1}`}
                      onClick={() => {
                        setSelectedAssetId(draft.asset_id);
                        setDialog(null);
                        pendingSeek.current = { time: clip.start, play: true };
                        seek(clip.start, clip.end);
                      }}
                    >
                      <Play size={15} />
                    </button>
                  </div>
                ))}
              </div>
              {!draft.clips.length && (
                <Empty
                  title="Not enough evidence for a cut"
                  detail="Try a more specific query or a different evidence mode."
                />
              )}
              <div className="modal-actions">
                <span className="muted">
                  {draft.clips.length} clips ·{" "}
                  {timecode(
                    draft.clips.reduce(
                      (sum, clip) => sum + clip.end - clip.start,
                      0,
                    ),
                  )}
                </span>
                <button className="button" onClick={() => setDialog(null)}>
                  Keep searching
                </button>
                <button
                  className="button primary"
                  disabled={!draft.clips.length || !canEdit || planBusy}
                  onClick={applyPlan}
                >
                  <Scissors size={14} />
                  Use this draft
                </button>
              </div>
            </>
          )}
        </Modal>
      )}
    </div>
  );
}
