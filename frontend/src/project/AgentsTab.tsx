import { Fragment, useState } from "react";
import { api } from "../api";
import type { TabProps } from "../pages/ProjectView";
import type { AgentRun, Task } from "../types";
import { Empty, ErrorText, StatusBadge, money, time, useLoad } from "../components/ui";

const ROSTER = [
  { role: "planner", name: "Planner", purpose: "Requirements → specification and task graph" },
  { role: "developer", name: "Developer", purpose: "Implements tasks and review fixes" },
  { role: "tester", name: "Tester", purpose: "Runs syntax checks and pytest in the sandbox" },
  { role: "debugger", name: "Debugger", purpose: "Diagnoses failures, proposes minimal fixes" },
  { role: "reviewer", name: "Reviewer", purpose: "Checks requirements, quality and security" },
];

export default function AgentsTab({ project, version, events }: TabProps) {
  const runs = useLoad(() => api<AgentRun[]>(`/api/projects/${project.id}/agent-runs`), [project.id, version]);
  const tasks = useLoad(() => api<Task[]>(`/api/projects/${project.id}/tasks`), [project.id, version]);
  const [open, setOpen] = useState<number | null>(null);
  const taskKey = new Map((tasks.data ?? []).map((t) => [t.id, t.key]));
  const roster = project.mode === "single_agent"
    ? [{ role: "single_agent", name: "Single agent", purpose: "Baseline: whole project in one call" }, ROSTER[2]]
    : ROSTER;
  const testerRuns = events.filter((e) => e.type === "tests_started").length;

  if (runs.error) return <ErrorText error={runs.error} />;
  const data = runs.data ?? [];

  return (
    <div className="stack">
      <div className="grid grid-tiles" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(210px, 1fr))" }}>
        {roster.map((a) => {
          const mine = data.filter((r) => r.role === a.role);
          const activeNow = mine.some((r) => r.status === "running");
          const count = a.role === "tester" ? testerRuns : mine.length;
          return (
            <div key={a.role} className="card tile">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <b>{a.name}</b>
                {activeNow ? <StatusBadge status="running" live /> : <span className="small faint">{count} run(s)</span>}
              </div>
              <div className="small muted" style={{ margin: "4px 0 8px" }}>{a.purpose}</div>
              {a.role === "tester"
                ? <div className="small faint">Deterministic: verdicts come from real execution</div>
                : <div className="small faint">{money(mine.reduce((s, r) => s + r.cost_usd, 0))} · {(mine.reduce((s, r) => s + r.input_tokens + r.output_tokens, 0) / 1000).toFixed(1)}k tokens</div>}
            </div>
          );
        })}
      </div>

      <div className="card">
        <div className="card-head"><h3>Agent runs</h3><span className="small faint">Every model call, with validated structured output</span></div>
        {data.length === 0 && <Empty>No agent has run yet.</Empty>}
        {data.length > 0 && (
          <table>
            <thead><tr><th>Time</th><th>Agent</th><th>Task</th><th>Operation</th><th>Status</th><th>Duration</th><th>Tokens</th><th>Cost</th></tr></thead>
            <tbody>
              {data.map((r) => (
                <Fragment key={r.id}>
                  <tr className="clickable" onClick={() => setOpen(open === r.id ? null : r.id)}>
                    <td className="small muted nowrap">{time(r.started_at)}</td>
                    <td className="nowrap"><b>{r.role}</b></td>
                    <td className="mono small">{r.task_id ? taskKey.get(r.task_id) : "—"}</td>
                    <td>{r.input_summary}</td>
                    <td><StatusBadge status={r.status} live={r.status === "running"} /></td>
                    <td className="small nowrap">{(r.duration_ms / 1000).toFixed(1)}s</td>
                    <td className="small nowrap">{r.input_tokens.toLocaleString()} / {r.output_tokens.toLocaleString()}</td>
                    <td className="small nowrap">{money(r.cost_usd)}</td>
                  </tr>
                  {open === r.id && (
                    <tr>
                      <td colSpan={8}>
                        {r.error && <div className="banner bad small" style={{ marginBottom: 8 }}>{r.error}</div>}
                        <div className="small faint" style={{ marginBottom: 6 }}>Model: {r.model ?? "—"}</div>
                        <pre className="log">{r.output ? JSON.stringify(r.output, null, 2) : "No output"}</pre>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
