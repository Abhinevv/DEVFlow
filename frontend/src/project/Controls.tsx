import { useState } from "react";
import { api } from "../api";
import type { ProjectDetail } from "../types";
import { ErrorText } from "../components/ui";

/** Human-in-the-loop controls (FR-17): plan, approve, pause, resume, cancel. */
export default function Controls({ project, onChange, onPlanTab }: { project: ProjectDetail; onChange: (p: ProjectDetail) => void; onPlanTab: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const { status, mode, control } = project;

  const act = async (action: string, body: unknown = {}) => {
    if (action === "cancel" && !window.confirm("Cancel this workflow? It cannot be resumed afterwards.")) return;
    setBusy(action);
    setError(null);
    try {
      onChange(await api<ProjectDetail>(`/api/projects/${project.id}/${action}`, { method: "POST", body }));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  };

  const buttons = [];
  if (mode === "multi_agent" && (status === "draft" || status === "plan_rejected")) {
    buttons.push(<button key="plan" className="primary" disabled={!!busy} onClick={() => act("plan")}>Generate plan</button>);
  }
  if (mode === "single_agent" && status === "draft") {
    buttons.push(<button key="start" className="primary" disabled={!!busy} onClick={() => act("start")}>Run baseline</button>);
  }
  if (status === "awaiting_approval") {
    buttons.push(<button key="review" className="primary" onClick={onPlanTab}>Review plan</button>);
  }
  if (status === "running" || status === "queued") {
    buttons.push(<button key="pause" disabled={!!busy || control === "pause"} onClick={() => act("pause")}>{control === "pause" ? "Pausing…" : "Pause"}</button>);
  }
  if (status === "paused" || status === "blocked") {
    buttons.push(<button key="resume" className="primary" disabled={!!busy} onClick={() => act("resume")}>Resume</button>);
  }
  if (!["completed", "partial", "failed", "cancelled"].includes(status)) {
    buttons.push(<button key="cancel" className="danger" disabled={!!busy || control === "cancel"} onClick={() => act("cancel")}>
      {control === "cancel" ? "Cancelling…" : "Cancel"}</button>);
  }

  return (
    <div className="stack" style={{ gap: 6, alignItems: "flex-end" }}>
      <div className="row">{buttons}</div>
      <ErrorText error={error} />
    </div>
  );
}
