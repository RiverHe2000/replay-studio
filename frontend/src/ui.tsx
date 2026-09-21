import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import {
  AlertCircle,
  Check,
  ChevronRight,
  Film,
  LoaderCircle,
  X,
} from "lucide-react";

export function timecode(value: number | null | undefined, precise = false) {
  if (value == null || !Number.isFinite(value)) return "—";
  const safe = Math.max(0, value);
  const seconds = Math.floor(safe % 60)
    .toString()
    .padStart(2, "0");
  const minutes = Math.floor(safe / 60);
  const base =
    minutes >= 60
      ? `${Math.floor(minutes / 60)}:${(minutes % 60).toString().padStart(2, "0")}:${seconds}`
      : `${minutes.toString().padStart(2, "0")}:${seconds}`;
  return precise ? `${base}.${Math.floor((safe % 1) * 10)}` : base;
}
export function bytes(value: number) {
  return value >= 1024 ** 3
    ? `${(value / 1024 ** 3).toFixed(1)} GB`
    : `${(value / 1024 ** 2).toFixed(1)} MB`;
}
export function dateLabel(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
export const activeStatus = (status: string) =>
  [
    "queued",
    "pending",
    "running",
    "processing",
    "retrying",
    "waiting",
  ].includes(status);
export const doneStatus = (status: string) =>
  ["ready", "complete", "completed", "succeeded", "success"].includes(status);
export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="brand">
      <span className="brand-mark">
        <span />
        <ChevronRight size={24} strokeWidth={3} />
      </span>
      {!compact && (
        <span>
          replay<span className="brand-light">studio</span>
        </span>
      )}
    </div>
  );
}
export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <span className="spinner" role="status">
      <LoaderCircle size={16} className="spin" />
      <span>{label}</span>
    </span>
  );
}
export function Status({ value }: { value: string }) {
  const good = doneStatus(value);
  const failed = ["failed", "error", "blocked"].includes(value);
  return (
    <span
      className={`status ${good ? "good" : failed ? "bad" : activeStatus(value) ? "working" : ""}`}
    >
      {good ? <Check size={11} /> : <span className="status-dot" />}
      {value.replaceAll("_", " ")}
    </span>
  );
}
export function Notice({
  children,
  warning = false,
}: {
  children: ReactNode;
  warning?: boolean;
}) {
  return (
    <div
      className={`notice ${warning ? "warning" : ""}`}
      role={warning ? "alert" : "status"}
    >
      <AlertCircle size={15} />
      <div>{children}</div>
    </div>
  );
}
export function Empty({
  title,
  detail,
  children,
}: {
  title: string;
  detail: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty">
      <span className="empty-icon">
        <Film size={25} strokeWidth={1.4} />
      </span>
      <h3>{title}</h3>
      <p>{detail}</p>
      {children}
    </div>
  );
}
export function Modal({
  title,
  eyebrow,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  eyebrow?: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const panel = ref.current;
    const focusable = () =>
      Array.from(
        panel?.querySelectorAll<HTMLElement>(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex="0"]',
        ) ?? [],
      ).filter((el) => el.offsetParent !== null);
    (focusable()[0] ?? panel)?.focus();
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
      if (event.key === "Tab") {
        const elements = focusable();
        const first = elements[0];
        const last = elements[elements.length - 1];
        if (!first) {
          event.preventDefault();
          return;
        }
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.removeEventListener("keydown", handler);
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className={`modal ${wide ? "wide" : ""}`}
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <header>
          <div>
            {eyebrow && <span className="eyebrow">{eyebrow}</span>}
            <h2>{title}</h2>
          </div>
          <button
            className="icon-button"
            aria-label="Close dialog"
            onClick={onClose}
          >
            <X size={19} />
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}
