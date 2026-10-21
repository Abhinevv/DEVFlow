import type { TabProps } from "../pages/ProjectView";
import type { WorkflowEvent } from "../types";
import { Tile, duration, money, pct } from "../components/ui";
import { EventRow } from "./HistoryTab";

type StageState = "todo" | "active" | "done" | "failed";

const STAGE_OF_AGENT: Record<string, string> = { planner: "plan", developer: "develop", tester: "test", debugger: "debug", reviewer: "review", single_agent: "develop" };

function currentStage(events: WorkflowEvent[]): string | null {
  if (events.some((e) => e.type === "phase" && e.message === "Final validation")) return "deliver";
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.type === "delivered" || e.type === "final_validation") return "deliver";
    if (e.type === "debug_attempt") return "debug";
    if (e.type === "revision_started") return "review";
    if (e.type === "tests_started") return "test";
    if (e.type === "plan_ready") return "approve";
    if (e.type === "agent_started" && e.agent) return STAGE_OF_AGENT[e.agent] ?? null;
  }
  return null;
}

export default function Overview({ project, events }: TabProps) {
  const m = project.metrics;
  const has = (type: string) => events.some((e) => e.type === type);
  const running = ["planning", "queued", "running"].includes(project.status);
  const active = running ? currentStage(events) : project.status === "awaiting_approval" ? "approve" : null;
  const failedTerminal = ["failed", "blocked"].includes(project.status);
  const single = project.mode === "single_agent";

  const stages: { id: string; name: string; note: string; state: StageState }[] = [
    { id: "plan", name: "Plan", note: project.plan ? `v${project.plan_version} · ${project.plan.tasks.length} tasks` : single ? "skipped" : "—", state: project.plan || single ? "done" : "todo" },
    { id: "approve", name: "Approve", note: has("plan_approved") ? "approved" : single ? "skipped" : "—", state: has("plan_approved") || single ? "done" : "todo" },
    { id: "develop", name: "Develop", note: single ? "one pass" : `${m.tasks_completed}/${m.tasks_total} tasks`, state: (single ? has("files_changed") : m.tasks_total > 0 && m.tasks_completed === m.tasks_total) ? "done" : "todo" },
    { id: "test", name: "Test", note: `${m.test_runs} run(s)`, state: m.test_runs ? "done" : "todo" },
    { id: "debug", name: "Debug", note: `${m.retry_count} attempt(s)`, state: m.retry_count ? "done" : "todo" },
    { id: "review", name: "Review", note: single ? "skipped" : `${project.review_round} revision(s)`, state: has("review_completed") ? "done" : "todo" },
    { id: "deliver", name: "Deliver", note: project.has_package ? "packaged" : "—", state: has("delivered") ? "done" : "todo" },
  ];
  for (const s of stages) {
    if (s.id === active) s.state = "active";
  }
  if (failedTerminal) {
    const last = active ?? [...stages].reverse().find((s) => s.state === "done")?.id;
    const target = stages.find((s) => s.id === last);
    if (target) target.state = "failed";
  }

  const runningAgent = (() => {
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.type === "agent_finished" || e.type === "agent_failed") return null;
      if (e.type === "agent_started") return e;
      if (e.type === "tests_started") return e;
    }
    return null;
  })();

  const pending = project.status === "awaiting_approval";
  const tone = { completed: "ok", partial: "warn", failed: "bad", blocked: "warn", cancelled: "", paused: "warn" }[project.status as string];

  return (
    <div className="stack">
      {pending && (
        <div className="banner info">
          <div className="grow"><b>Plan ready for approval.</b> No code is generated or executed until you approve it.
            {project.plan?.clarifications_needed.length ? ` The planner raised ${project.plan.clarifications_needed.length} clarification question(s).` : ""}</div>
        </div>
      )}
      {tone !== undefined && project.status_reason && (
        <div className={`banner ${tone}`}><div className="grow"><b>{project.status === "completed" ? "Delivered." : project.status === "blocked" ? "Needs your decision." : "Workflow stopped."}</b> {project.status_reason}</div></div>
      )}

      <div className="card card-body">
        <div className="section-title">Workflow</div>
        <div className="pipeline" role="list">
          {stages.map((s) => (
            <div key={s.id} role="listitem" className={`stage ${s.state}`} aria-current={s.state === "active" ? "step" : undefined}>
              <div className="node">{s.state === "done" ? "✓" : s.state === "failed" ? "!" : stages.indexOf(s) + 1}</div>
              <div className="name">{s.name}</div>
              <div className="note">{s.note}</div>
            </div>
          ))}
        </div>
        {runningAgent && running && (
          <p className="small muted" style={{ marginBottom: 0 }}>
            <span className="badge accent live"><span className="dot" />{runningAgent.agent ?? "tester"}</span> {runningAgent.message}
          </p>
        )}
      </div>

      <div className="grid grid-tiles">
        <Tile label="Tasks" value={`${m.tasks_completed}/${m.tasks_total}`} sub="completed" />
        <Tile label="Test pass rate" value={pct(m.test_pass_rate)} sub={m.final_tests ? `${m.final_tests.passed}/${m.final_tests.total} in final run` : "no final run yet"} />
        <Tile label="Debug attempts" value={m.retry_count} sub={m.recovery_rate !== null ? `${pct(m.recovery_rate)} recovered` : "no failures"} />
        <Tile label="Active time" value={running && !m.duration_seconds ? "…" : duration(m.duration_seconds)}
          sub={running ? `tallied when the run stops · ${m.model_calls} call(s)` : `${m.model_calls} model call(s)`} />
        <Tile label="Model cost" value={money(m.cost_usd)} sub={`${(m.input_tokens / 1000).toFixed(1)}k in · ${(m.output_tokens / 1000).toFixed(1)}k out`} />
        <Tile label="Safety" value={m.safety_violations} sub={`violation(s) · ${m.human_interventions} intervention(s)`} />
      </div>

      <div className="card">
        <div className="card-head"><h3>Recent activity</h3><span className="small faint">{events.length} events</span></div>
        <div className="card-body timeline">
          {events.length === 0 && <div className="muted small">Waiting for events…</div>}
          {events.slice(-14).reverse().map((e) => <EventRow key={e.id} event={e} />)}
        </div>
      </div>
    </div>
  );
}
