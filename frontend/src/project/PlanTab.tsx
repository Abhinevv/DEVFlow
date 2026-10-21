import { useState } from "react";
import { api } from "../api";
import type { TabProps } from "../pages/ProjectView";
import type { PlanTask, ProjectDetail } from "../types";
import { Empty, ErrorText } from "../components/ui";

function List({ title, items, tone }: { title: string; items: string[]; tone?: string }) {
  if (!items.length) return null;
  return (
    <div className={tone ? `banner ${tone}` : ""} style={tone ? { display: "block" } : undefined}>
      <div className="section-title">{title}</div>
      <ul>{items.map((item, i) => <li key={i}>{item}</li>)}</ul>
    </div>
  );
}

/** Group tasks into dependency levels so the graph reads left to right. */
function levels(tasks: PlanTask[]): PlanTask[][] {
  const depth = new Map<string, number>();
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const visit = (t: PlanTask, seen: Set<string>): number => {
    if (depth.has(t.id)) return depth.get(t.id)!;
    if (seen.has(t.id)) return 0;
    seen.add(t.id);
    const d = t.depends_on.length ? 1 + Math.max(...t.depends_on.map((id) => (byId.get(id) ? visit(byId.get(id)!, seen) : 0))) : 0;
    depth.set(t.id, d);
    return d;
  };
  tasks.forEach((t) => visit(t, new Set()));
  const out: PlanTask[][] = [];
  tasks.forEach((t) => { (out[depth.get(t.id)!] ??= []).push(t); });
  return out;
}

export default function PlanTab({ project, onChange }: TabProps & { onChange: (p: ProjectDetail) => void }) {
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const plan = project.plan;
  const awaiting = project.status === "awaiting_approval";

  const decide = async (action: "approve" | "reject" | "revise") => {
    setBusy(true);
    setError(null);
    try {
      const body = action === "revise" ? { feedback } : { comment: feedback || null };
      onChange(await api<ProjectDetail>(`/api/projects/${project.id}/plan/${action}`, { method: "POST", body }));
      setFeedback("");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  if (!plan) {
    return <div className="card"><Empty>{project.status === "planning" ? "The planner is analysing the requirements…" :
      project.mode === "single_agent" ? "Single-agent baseline runs skip planning." : "No plan yet. Generate one from the header."}</Empty></div>;
  }

  return (
    <div className="stack">
      {awaiting && (
        <div className="card card-body">
          <h3>Approve this plan?</h3>
          <p className="small muted" style={{ marginTop: 4 }}>Approving starts code generation and sandboxed test execution. Request changes to re-plan with your feedback, or answer the planner's questions there.</p>
          <textarea value={feedback} onChange={(e) => setFeedback(e.target.value)} rows={3} maxLength={4000}
            placeholder="Optional comment, or the changes you want (required to request changes)" style={{ minHeight: 72 }} aria-label="Plan feedback" />
          <div className="row" style={{ marginTop: 10, justifyContent: "flex-end" }}>
            <ErrorText error={error} />
            <button className="danger" disabled={busy} onClick={() => decide("reject")}>Reject</button>
            <button disabled={busy || feedback.trim().length < 3} onClick={() => decide("revise")}>Request changes</button>
            <button className="primary" disabled={busy} onClick={() => decide("approve")}>Approve and start</button>
          </div>
        </div>
      )}

      <div className="card card-body stack">
        <div>
          <div className="section-title">Plan v{project.plan_version}</div>
          <p style={{ margin: 0 }}>{plan.summary}</p>
        </div>
        <List title="Clarifications needed" items={plan.clarifications_needed} tone="warn" />
        <div className="grid grid-2">
          <List title="Functional requirements" items={plan.functional_requirements} />
          <List title="Non-functional requirements" items={plan.non_functional_requirements} />
          <List title="Acceptance criteria" items={plan.acceptance_criteria} />
          <List title="Assumptions" items={plan.assumptions} />
          <List title="Constraints" items={plan.constraints} />
          <div>
            <div className="section-title">Technology stack</div>
            <div className="chips">{plan.tech_stack.map((t) => <span key={t} className="chip">{t}</span>)}</div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h3>Task graph</h3><span className="small faint">Columns are dependency levels; a task starts once everything it depends on is complete.</span></div>
        <div className="card-body" style={{ overflowX: "auto" }}>
          <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
            {levels(plan.tasks).map((level, i) => (
              <div key={i} style={{ minWidth: 240, flex: "1 0 240px" }}>
                <div className="section-title">Level {i + 1}</div>
                {level.map((t) => (
                  <div key={t.id} className="task-card">
                    <div className="row" style={{ justifyContent: "space-between" }}><span className="key">{t.id}</span>
                      {t.depends_on.length > 0 && <span className="small faint">after {t.depends_on.join(", ")}</span>}</div>
                    <div style={{ fontWeight: 600, margin: "2px 0 4px" }}>{t.title}</div>
                    <div className="small muted">{t.description}</div>
                    <details style={{ marginTop: 6 }} className="small">
                      <summary>Acceptance criteria and outputs</summary>
                      <ul style={{ marginTop: 6 }}>{t.acceptance_criteria.map((c, j) => <li key={j}>{c}</li>)}</ul>
                      <div className="chips" style={{ marginTop: 6 }}>{t.expected_outputs.map((o) => <span key={o} className="chip">{o}</span>)}</div>
                    </details>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="grid grid-2">
        <div className="card">
          <div className="card-head"><h3>Components</h3></div>
          {plan.components.map((c) => (
            <div key={c.name} className="list-item">
              <b>{c.name}</b> <span className="muted">— {c.responsibility}</span>
              <div className="chips" style={{ marginTop: 4 }}>{c.interfaces.map((x) => <span key={x} className="chip">{x}</span>)}</div>
            </div>
          ))}
        </div>
        <div className="card">
          <div className="card-head"><h3>Expected directory structure</h3></div>
          <div className="card-body"><pre className="faint">{plan.directory_structure.join("\n")}</pre></div>
        </div>
      </div>
    </div>
  );
}
