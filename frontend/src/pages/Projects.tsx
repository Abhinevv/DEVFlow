import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import type { AggregateMetrics, Project } from "../types";
import { Empty, ErrorText, StatusBadge, Tile, duration, label, money, pct, relative, useLoad } from "../components/ui";

const ACTIVE = new Set(["planning", "queued", "running"]);

export default function Projects() {
  const navigate = useNavigate();
  const projects = useLoad(() => api<Project[]>("/api/projects"), []);
  const metrics = useLoad(() => api<{ multi_agent: AggregateMetrics; single_agent: AggregateMetrics }>("/api/metrics"), []);
  const m = metrics.data?.multi_agent;
  const s = metrics.data?.single_agent;

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Projects</h1>
          <p>Describe software in plain language; planner, developer, tester, debugger and reviewer agents build and validate it.</p>
        </div>
        <button className="primary" onClick={() => navigate("/new")}>New project</button>
      </div>

      {m && m.finished > 0 && (
        <section style={{ marginBottom: 24 }}>
          <div className="section-title">Evaluation across finished projects</div>
          <div className="grid grid-tiles">
            <Tile label="Completion rate" value={pct(m.task_completion_rate)} sub={`${m.finished} multi-agent run(s)`} />
            <Tile label="Test pass rate" value={pct(m.mean_test_pass_rate)} sub="mean of final runs" />
            <Tile label="Recovery rate" value={pct(m.recovery_rate)} sub="failed tasks repaired" />
            <Tile label="Mean duration" value={duration(m.mean_duration_seconds)} sub={`${m.mean_retry_count ?? "—"} avg debug retries`} />
            <Tile label="Cost / success" value={money(m.cost_per_successful_task)} sub={`${money(m.total_cost_usd)} total`} />
            <Tile label="Human intervention" value={pct(m.human_intervention_rate)} sub={`${m.safety_violations} safety violation(s)`} />
          </div>
          {s && s.finished > 0 && (
            <p className="small muted" style={{ marginTop: 8 }}>
              Single-agent baseline: {pct(s.task_completion_rate)} completion, {pct(s.mean_test_pass_rate)} test pass rate over {s.finished} run(s).
            </p>
          )}
        </section>
      )}

      <div className="card">
        <ErrorText error={projects.error} />
        {projects.data && projects.data.length === 0 && (
          <Empty>
            <h3>No projects yet</h3>
            <p>Create one to see the agents plan, build, test, debug and review it.</p>
            <button className="primary" onClick={() => navigate("/new")}>Create your first project</button>
          </Empty>
        )}
        {projects.data && projects.data.length > 0 && (
          <table>
            <thead>
              <tr><th>Name</th><th>Status</th><th>Mode</th><th>Template</th><th>Updated</th></tr>
            </thead>
            <tbody>
              {projects.data.map((p) => (
                <tr key={p.id} className="clickable" onClick={() => navigate(`/projects/${p.id}`)}>
                  <td><Link to={`/projects/${p.id}`} onClick={(e) => e.stopPropagation()}><b>{p.name}</b></Link>
                    {p.status_reason && <div className="small faint" style={{ maxWidth: 520 }}>{p.status_reason}</div>}</td>
                  <td><StatusBadge status={p.status} live={ACTIVE.has(p.status)} /></td>
                  <td className="small muted nowrap">{label(p.mode)}</td>
                  <td className="small muted mono">{p.template}</td>
                  <td className="small muted nowrap">{relative(p.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </main>
  );
}
