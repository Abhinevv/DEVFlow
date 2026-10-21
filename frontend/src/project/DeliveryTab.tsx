import { useState } from "react";
import { api, download, downloadText } from "../api";
import type { TabProps } from "../pages/ProjectView";
import type { DeliveryReport } from "../types";
import { Empty, ErrorText, SeverityBadge, StatusBadge, useLoad } from "../components/ui";

export default function DeliveryTab({ project, version }: TabProps) {
  const report = useLoad(() => api<DeliveryReport>(`/api/projects/${project.id}/delivery`), [project.id, version]);
  const [error, setError] = useState<unknown>(null);
  const r = report.data;

  if (report.error) return <ErrorText error={report.error} />;
  if (!r) return null;
  if (!r.package && !r.final_tests) {
    return <div className="card"><Empty>The delivery package is produced after final validation.</Empty></div>;
  }
  const ft = r.final_tests;
  const config = r.config as { llm_model?: string; template_version?: string; executor?: string; limits?: Record<string, number> };

  return (
    <div className="stack">
      <div className="card card-body row" style={{ justifyContent: "space-between" }}>
        <div>
          <div className="row"><h2>Delivery</h2><StatusBadge status={r.project.status} /></div>
          <div className="small muted" style={{ marginTop: 4 }}>{r.project.status_reason}</div>
        </div>
        <div className="row">
          <button onClick={() => downloadText(r.markdown, `devflow-report-${project.id}.md`)}>Download report</button>
          <button className="primary" disabled={!r.package}
            onClick={() => download(`/api/projects/${project.id}/package`, r.package!).catch(setError)}>Download package (.zip)</button>
        </div>
      </div>
      <ErrorText error={error} />

      <div className="grid grid-2">
        <div className="card">
          <div className="card-head"><h3>Final validation</h3>{ft && <span className={`badge ${ft.success ? "ok" : "bad"}`}>{ft.success ? "pass" : "fail"}</span>}</div>
          <div className="card-body small stack" style={{ gap: 8 }}>
            {ft ? <>
              <div>{ft.summary}</div>
              <div className="muted">Executed with <b>{ft.executor}</b> ({ft.isolated ? "isolated container" : "not isolated"})</div>
              {ft.failures.map((f, i) => <div key={i} className="mono sev-high">{f.nodeid}: {f.message}</div>)}
              <div className="muted">Static issues: {ft.checks.static_issues?.length ?? 0} · Security issues: {ft.checks.security_issues?.length ?? 0}</div>
            </> : <div className="muted">No final run recorded.</div>}
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h3>Setup instructions</h3></div>
          <div className="card-body">
            <pre className="log">{`python -m venv .venv\n# Windows: .venv\\Scripts\\activate\n# macOS/Linux: source .venv/bin/activate\npip install -r requirements.txt\npytest\nuvicorn app.main:app --reload`}</pre>
            <div className="small faint" style={{ marginTop: 6 }}>The package contains <span className="mono">source/</span>, <span className="mono">DEVFLOW_REPORT.md</span> and <span className="mono">devflow_manifest.json</span>.</div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h3>Tasks</h3></div>
        <table>
          <thead><tr><th>Task</th><th>Title</th><th>Status</th><th>Debug attempts</th></tr></thead>
          <tbody>{r.tasks.map((t) => (
            <tr key={t.id}><td className="mono">{t.id}</td><td>{t.title}</td><td><StatusBadge status={t.status} /></td><td>{t.debug_attempts}</td></tr>
          ))}</tbody>
        </table>
      </div>

      <div className="grid grid-2">
        <div className="card">
          <div className="card-head"><h3>Open findings</h3><span className="small faint">{r.resolved_findings.length} resolved</span></div>
          {r.open_findings.length === 0 && <div className="card-body small muted">None.</div>}
          {r.open_findings.map((f, i) => (
            <div key={i} className="list-item small">
              <div className="row" style={{ gap: 8 }}><SeverityBadge severity={f.severity} /><span className="mono">{f.file || "—"}{f.line ? `:${f.line}` : ""}</span></div>
              <div style={{ marginTop: 4 }}>{f.description}</div>
            </div>
          ))}
        </div>
        <div className="card">
          <div className="card-head"><h3>Known limitations</h3></div>
          <div className="card-body small"><ul>{r.limitations.map((l, i) => <li key={i}>{l}</li>)}</ul></div>
        </div>
      </div>

      <div className="card card-body small muted">
        <div className="section-title">Reproducibility</div>
        Model <span className="mono">{config.llm_model ?? "—"}</span> · template <span className="mono">{r.project.template} v{config.template_version ?? "?"}</span> ·
        executor <span className="mono">{config.executor ?? "—"}</span>
        {config.limits && <> · limits <span className="mono">{Object.entries(config.limits).map(([k, v]) => `${k}=${v}`).join(", ")}</span></>}
      </div>
    </div>
  );
}
