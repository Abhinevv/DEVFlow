import { useState } from "react";
import { api } from "../api";
import type { TabProps } from "../pages/ProjectView";
import type { Finding } from "../types";
import { Empty, ErrorText, SeverityBadge, StatusBadge, useLoad } from "../components/ui";

const ORDER = ["critical", "high", "medium", "low", "info"];

export default function ReviewTab({ project, version }: TabProps) {
  const findings = useLoad(() => api<Finding[]>(`/api/projects/${project.id}/findings`), [project.id, version]);
  const [filter, setFilter] = useState<"open" | "all">("all");

  if (findings.error) return <ErrorText error={findings.error} />;
  const all = findings.data ?? [];
  if (findings.data && !all.length) return <div className="card"><Empty>The reviewer runs after all tasks pass their tests.</Empty></div>;

  const shown = all.filter((f) => filter === "all" || f.status === "open");
  const rounds = [...new Set(shown.map((f) => f.round))].sort((a, b) => b - a);
  const open = all.filter((f) => f.status === "open");

  return (
    <div className="stack">
      <div className="grid grid-tiles">
        {ORDER.map((sev) => (
          <div key={sev} className="card tile">
            <div className="label">{sev}</div>
            <div className={`value sev-${sev}`}>{open.filter((f) => f.severity === sev).length}</div>
            <div className="sub">open · {all.filter((f) => f.severity === sev).length} total</div>
          </div>
        ))}
      </div>
      <div className="segmented" role="group" aria-label="Filter findings">
        <button className={filter === "all" ? "on" : ""} onClick={() => setFilter("all")}>All findings</button>
        <button className={filter === "open" ? "on" : ""} onClick={() => setFilter("open")}>Open only ({open.length})</button>
      </div>
      {rounds.map((round) => (
        <div key={round} className="card">
          <div className="card-head"><h3>Review round {round + 1}</h3></div>
          {shown.filter((f) => f.round === round).sort((a, b) => ORDER.indexOf(a.severity) - ORDER.indexOf(b.severity)).map((f) => (
            <div key={f.id} className="list-item">
              <div className="row" style={{ marginBottom: 4 }}>
                <SeverityBadge severity={f.severity} />
                <span className="badge">{f.category}</span>
                <span className="small faint">{f.source}</span>
                {f.file && <span className="mono small">{f.file}{f.line ? `:${f.line}` : ""}</span>}
                <StatusBadge status={f.status} />
                {f.actionable && <span className="small faint">actionable</span>}
              </div>
              <div>{f.description}</div>
              {f.recommendation && <div className="small muted" style={{ marginTop: 2 }}>Recommendation: {f.recommendation}</div>}
            </div>
          ))}
        </div>
      ))}
      {shown.length === 0 && <div className="card"><Empty>No open findings.</Empty></div>}
    </div>
  );
}
