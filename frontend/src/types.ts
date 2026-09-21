export type Role = "owner" | "editor" | "viewer";
export type Mode = "fusion" | "speech_ocr" | "speech";
export interface User {
  id: string;
  email: string;
  name: string;
}
export interface Auth {
  user: User;
  csrf_token: string;
}
export interface ProjectSummary {
  id: string;
  name: string;
  role: Role;
  created_at: string;
  updated_at: string;
}
export interface Asset {
  id: string;
  project_id: string;
  name: string;
  size: number;
  duration: number | null;
  status: string;
  run_id: string | null;
  created_at: string;
}
export interface Clip {
  id: string;
  start: number;
  end: number;
  title: string;
  subtitle: string;
  evidence_ids: string[];
}
export interface Timeline {
  version: number;
  asset_id: string | null;
  run_id: string | null;
  clips: Clip[];
  updated_at?: string;
}
export interface Job {
  id: string;
  kind: string;
  status: string;
  asset_id?: string;
  run_id?: string;
  queue?: string;
  attempts?: number;
  error?: string | null;
  progress?: number;
  created_at?: string;
}
export interface Export {
  id: string;
  status: string;
  version?: number;
  timeline_version?: number;
  created_at?: string;
  error?: string | null;
}
export interface Member {
  user_id: string;
  id?: string;
  name?: string;
  email: string;
  role: Role;
}
export interface Comment {
  id: string;
  asset_id?: string | null;
  text: string;
  time?: number | null;
  created_at: string;
  user_id?: string;
  name?: string;
  author_name?: string;
  author?: { name: string; email?: string };
  user?: { name: string; email?: string };
}
export interface Project extends ProjectSummary {
  assets: Asset[];
  timeline: Timeline;
  jobs: Job[];
  exports: Export[];
  members: Member[];
  comments: Comment[];
}
export interface Evidence {
  modality: string;
  text: string;
  time: number;
}
export interface Hit {
  id: string;
  start: number;
  end: number;
  score: number;
  text: string;
  modalities: string[];
  frame: string | null;
  evidence: Evidence[];
  uncertain: boolean;
}
export interface SearchResult {
  hits: Hit[];
  mode: Mode;
  warnings: string[];
}
export interface Plan {
  clips: Clip[];
  warnings: string[];
  strategy: string;
  run_id: string;
  asset_id: string;
}
export interface Transcript {
  segments: { start: number; end: number; text: string }[];
  status: string;
  backend: string;
}
export interface UploadSession {
  id: string;
  offset: number;
  chunk_size: number;
  max_size: number;
  size?: number;
  status?: string;
}
export interface UploadState {
  name: string;
  total: number;
  offset: number;
  status: "uploading" | "paused" | "finalizing" | "complete" | "error";
  error?: string;
}
export type CapabilityMap = Record<string, unknown>;
