import { useState } from "react";
import { api } from "../api";
import type { TabProps } from "../pages/ProjectView";
import type { Issue, Task, TestRun } from "../types";
import { Empty, ErrorText, SeverityBadge, time, useLoad } from "../components/ui";

const KIND_LABEL: Record<string, string> = {
  task: "Task validation", retest: "Re-run of failed tests", regression: "Regression suite", revise: "Revision validation", final: "Final validation",
};

function Issues({ title, issues }: { title: string; issues: Issue[] }) {
  return (
    <div>
      <div className="section-title">{title} ({issues.length})</div>
      {issues.length === 0 ? <div className="small muted">None.</div> : issues.map((i, n) => (
        <div key={n} className="row small" style={{ gap: 8, marginBottom: 4 }}>
          <SeverityBadge severity={i.severity} /><span className="mono">{i.code}</span><span className="mono faint">{i.file}:{i.line}</span><span>{i.message}</span>
        </div>
      ))}
    </div>
  );
}

export default function TestsTab({ project, version }: TabProps) {
  const runs = useLoad(() => api<TestRun[]>(`/api/projects/${project.id}/tests`), [project.id, version]);
  const tasks = useLoad(() => api<Task[]>(`/api/projects/${project.id}/tasks`), [project.id, version]);
  const [open, setOpen] = useState<number | null>(null);
  const taskKey = new Map((tasks.data ?? []).map((t) => [t.id, t.key]));

  if (runs.error) return <ErrorText error={runs.error} />;
  const data = [...(runs.data ?? [])].reverse();
  if (runs.data && !data.length) return <div className="card"><Empty>No test runs yet.</Empty></div>;
  const latest = data[0];
  const totals = data.reduce((acc, r) => ({ runs: acc.runs + 1, failedRuns: acc.failedRuns + (r.success ? 0 : 1) }), { runs: 0, failedRuns: 0 });

  return (
    <div className="stack">
      {latest && (
        <div className="grid grid-tiles">
          <div className="card tile"><div className="label">Latest run</div><div className="value" style={{ color: latest.success ? "var(--ok)" : "var(--bad)" }}>{latest.success ? "Pass" : "Fail"}</div><div className="sub">{latest.summary}</div></div>
          <div className="card tile"><div className="label">Passed</div><div className="value">{latest.passed}</div><div className="sub">of {latest.total} in latest run</div></div>
          <div className="card tile"><div className="label">Runs</div><div className="value">{totals.runs}</div><div className="sub">{totals.failedRuns} failing run(s) recorded</div></div>
          <div className="card tile"><div className="label">Executor</div><div className="value" style={{ fontSize: 17 }}>{latest.executor}</div>
            <div className="sub">{latest.isolated ? "isolated container, no network" : "not isolated"}</div></div>
        </div>
      )}
      <div className="card">
        <div className="card-head"><h3>Test runs</h3><span className="small faint">Exit codes and counts come from real pytest runs (JUnit XML)</span></div>
        {data.map((r) => (
          <div key={r.id} className="list-item">
            <div className="row clickable" style={{ justifyContent: "space-between" }} onClick={() => setOpen(open === r.id ? null : r.id)}
              role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOpen(open === r.id ? null : r.id)} aria-expanded={open === r.id}>
              <div className="row">
                <span className={`badge ${r.success ? "ok" : "bad"}`}>{r.success ? "pass" : "fail"}</span>
                <b>{KIND_LABEL[r.kind] ?? r.kind}</b>
                {r.task_id && <span className="mono small">{taskKey.get(r.task_id)}</span>}
                <span className="small muted">{r.summary}</span>
              </div>
              <span className="small faint nowrap">exit {r.exit_code} · {(r.duration_ms / 1000).toFixed(1)}s · {time(r.created_at)}</span>
            </div>
            {open === r.id && (
              <div className="stack" style={{ marginTop: 12, gap: 12 }}>
                <div className="small mono faint">{r.command}</div>
                {r.failures.map((f, i) => (
                  <div key={i} className="banner bad" style={{ display: "block" }}>
                    <div className="mono small"><b>{f.nodeid}</b> <span className="faint">({f.kind})</span></div>
                    <div className="small" style={{ margin: "4px 0" }}>{f.message}</div>
                    {f.details && <details><summary className="small">Traceback</summary><pre className="log" style={{ marginTop: 6 }}>{f.details}</pre></details>}
                  </div>
                ))}
                {r.kind === "final" && (
                  <div className="grid grid-2">
                    <Issues title="Static analysis (Ruff)" issues={r.checks.static_issues ?? []} />
                    <Issues title="Security scan" issues={r.checks.security_issues ?? []} />
                  </div>
                )}
                {r.log && <details><summary className="small">Output log</summary><pre className="log" style={{ marginTop: 6 }}>{r.log}</pre></details>}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
