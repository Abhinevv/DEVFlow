import { api } from "../api";
import type { TabProps } from "../pages/ProjectView";
import type { Approval, WorkflowEvent } from "../types";
import { dateTime, label, time, useLoad } from "../components/ui";

const TONE: Record<string, string> = {
  tests_passed: "ok", task_completed: "ok", delivered: "ok", plan_approved: "ok", revision_validated: "ok", agent_finished: "ok",
  tests_failed: "bad", agent_failed: "bad", safety_violation: "bad", workflow_halted: "bad", model_error: "bad", workflow_error: "bad",
  debug_attempt: "warn", revision_rolled_back: "warn", review_limit: "warn", security_notice: "warn", workflow_paused: "warn",
  workflow_interrupted: "warn", executor_unavailable: "warn", limit_reached: "bad",
  plan_ready: "accent", task_started: "accent", review_completed: "accent", final_validation: "accent", revision_started: "accent",
};

export function EventRow({ event }: { event: WorkflowEvent }) {
  return (
    <div className={`event ${TONE[event.type] ?? ""}`}>
      <div className="t">{time(event.created_at)}</div>
      <div className="mark" />
      <div>
        <span className="small faint">{event.agent ? `${event.agent} · ` : ""}{label(event.type)}</span>
        <div>{event.message}</div>
      </div>
    </div>
  );
}

export default function HistoryTab({ project, events }: TabProps) {
  const approvals = useLoad(() => api<Approval[]>(`/api/projects/${project.id}/approvals`), [project.id, events.length]);
  const notable = events.filter((e) => e.type !== "agent_finished" && e.type !== "tests_started");

  return (
    <div className="grid" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(260px, 1fr)" }}>
      <div className="card">
        <div className="card-head"><h3>Workflow history</h3><span className="small faint">{events.length} recorded events</span></div>
        <div className="card-body timeline">{notable.map((e) => <EventRow key={e.id} event={e} />)}</div>
      </div>
      <div className="stack">
        <div className="card">
          <div className="card-head"><h3>Human decisions</h3></div>
          {(approvals.data ?? []).length === 0 && <div className="card-body small muted">None yet.</div>}
          {(approvals.data ?? []).map((a) => (
            <div key={a.id} className="list-item">
              <div><b>{label(a.kind)}</b> — {label(a.decision)}</div>
              {a.comment && <div className="small muted">“{a.comment}”</div>}
              <div className="small faint">{dateTime(a.created_at)}{a.plan_version ? ` · plan v${a.plan_version}` : ""}</div>
            </div>
          ))}
        </div>
        <div className="card card-body small">
          <div className="section-title">Run</div>
          <div>Created {dateTime(project.created_at)}</div>
          <div>Started {dateTime(project.started_at)}</div>
          <div>Finished {dateTime(project.finished_at)}</div>
        </div>
      </div>
    </div>
  );
}
