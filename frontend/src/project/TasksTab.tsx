import { useState } from "react";
import { api } from "../api";
import type { TabProps } from "../pages/ProjectView";
import type { Artifact, Task } from "../types";
import { Empty, ErrorText, StatusBadge, duration, useLoad } from "../components/ui";

const COLUMNS: Task["status"][] = ["pending", "running", "completed", "failed", "blocked"];

export default function TasksTab({ project, version }: TabProps) {
  const tasks = useLoad(() => api<Task[]>(`/api/projects/${project.id}/tasks`), [project.id, version]);
  const artifacts = useLoad(() => api<Artifact[]>(`/api/projects/${project.id}/artifacts`), [project.id, version]);
  const [open, setOpen] = useState<number | null>(null);

  if (tasks.error) return <ErrorText error={tasks.error} />;
  if (!tasks.data) return null;
  if (!tasks.data.length) return <div className="card"><Empty>Tasks appear here once a plan is generated.</Empty></div>;

  const done = new Set(tasks.data.filter((t) => t.status === "completed").map((t) => t.key));
  const selected = tasks.data.find((t) => t.id === open);

  return (
    <div className="stack">
      <div className="board">
        {COLUMNS.map((col) => {
          const items = tasks.data!.filter((t) => t.status === col);
          return (
            <div key={col} className="column">
              <div className="column-head"><StatusBadge status={col} live={col === "running"} /><span>{items.length}</span></div>
              {items.map((t) => {
                const waiting = t.depends_on.filter((d) => !done.has(d));
                return (
                  <div key={t.id} className={`task-card clickable ${open === t.id ? "selected" : ""}`} onClick={() => setOpen(open === t.id ? null : t.id)}
                    role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOpen(open === t.id ? null : t.id)}>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <span className="key">{t.key}</span>
                      {t.debug_attempts > 0 && <span className="badge warn">{t.debug_attempts} debug</span>}
                    </div>
                    <div style={{ fontWeight: 600, margin: "2px 0" }}>{t.title}</div>
                    {t.depends_on.length > 0 && (
                      <div className="small faint">depends on {t.depends_on.map((d) => <span key={d} className={done.has(d) ? "" : "sev-medium"}>{d} </span>)}</div>
                    )}
                    {col === "pending" && waiting.length > 0 && <div className="small faint">waiting for {waiting.join(", ")}</div>}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>

      {selected && (
        <div className="card">
          <div className="card-head">
            <div className="row"><span className="key mono">{selected.key}</span><h3>{selected.title}</h3><StatusBadge status={selected.status} /></div>
            <button className="ghost small" onClick={() => setOpen(null)}>Close</button>
          </div>
          <div className="card-body grid grid-2">
            <div className="stack" style={{ gap: 12 }}>
              <p style={{ margin: 0 }}>{selected.description}</p>
              <div>
                <div className="section-title">Acceptance criteria</div>
                <ul>{selected.acceptance_criteria.map((c, i) => <li key={i}>{c}</li>)}</ul>
              </div>
              <div className="small muted">
                Attempts: {selected.attempts} · Debug attempts: {selected.debug_attempts}
                {selected.started_at && selected.finished_at && <> · Took {duration((new Date(selected.finished_at).getTime() - new Date(selected.started_at).getTime()) / 1000)}</>}
              </div>
            </div>
            <div className="stack" style={{ gap: 12 }}>
              <div>
                <div className="section-title">Completion evidence</div>
                {Object.keys(selected.evidence).length ? <pre className="log">{JSON.stringify(selected.evidence, null, 2)}</pre> : <div className="small muted">None yet.</div>}
              </div>
              <div>
                <div className="section-title">File changes</div>
                {(artifacts.data ?? []).filter((a) => a.task_id === selected.id).map((a) => (
                  <div key={a.id} className="row small" style={{ gap: 8 }}>
                    <span className={`badge ${a.action === "rejected" ? "bad" : a.action === "create" ? "ok" : ""}`}>{a.action}</span>
                    <span className="mono">{a.path}</span>
                    {a.note && <span className="faint">{a.note}</span>}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
