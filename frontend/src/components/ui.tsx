import { useEffect, useState, type ReactNode } from "react";
import { ApiError } from "../api";

const STATUS_TONE: Record<string, string> = {
  completed: "ok", passed: "ok", success: "ok", resolved: "ok", approved: "ok", met: "ok",
  running: "accent", planning: "accent", queued: "accent",
  awaiting_approval: "info", draft: "", pending: "",
  partial: "warn", paused: "warn", blocked: "warn", superseded: "", partial_cov: "warn",
  failed: "bad", error: "bad", cancelled: "", plan_rejected: "bad", rejected: "bad", open: "warn", missing: "bad",
};

const STATUS_LABEL: Record<string, string> = {
  awaiting_approval: "Awaiting approval", plan_rejected: "Plan rejected", multi_agent: "Multi-agent", single_agent: "Single-agent",
};

export function label(status: string): string {
  return STATUS_LABEL[status] ?? status.charAt(0).toUpperCase() + status.slice(1).replace(/_/g, " ");
}

export function StatusBadge({ status, live }: { status: string; live?: boolean }) {
  const tone = STATUS_TONE[status] ?? "";
  return <span className={`badge ${tone} ${live ? "live" : ""}`}><span className="dot" />{label(status)}</span>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  const tone = severity === "critical" || severity === "high" ? "bad" : severity === "medium" ? "warn" : severity === "low" ? "info" : "";
  return <span className={`badge ${tone}`}>{severity}</span>;
}

export function Tile({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="card tile">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub !== undefined && <div className="sub">{sub}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null;
  const message = error instanceof ApiError || error instanceof Error ? error.message : String(error);
  return <div className="error-text" role="alert">{message}</div>;
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

export function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m < 60 ? `${m}m ${s}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
}

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value < 0.01 && value > 0 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

export function time(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function dateTime(iso: string | null): string {
  return iso ? new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—";
}

export function relative(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

/** Load data from an async function; reloads whenever `deps` change. */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | undefined; error: unknown; loading: boolean } {
  const [state, setState] = useState<{ data: T | undefined; error: unknown; loading: boolean }>({ data: undefined, error: null, loading: true });
  useEffect(() => {
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    fn().then(
      (data) => alive && setState({ data, error: null, loading: false }),
      (error) => alive && setState((s) => ({ data: s.data, error, loading: false })),
    );
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
