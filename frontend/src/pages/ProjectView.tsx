import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, streamEvents } from "../api";
import type { ProjectDetail, WorkflowEvent } from "../types";
import { ErrorText, Spinner, StatusBadge, label } from "../components/ui";
import Controls from "../project/Controls";
import Overview from "../project/Overview";
import PlanTab from "../project/PlanTab";
import TasksTab from "../project/TasksTab";
import AgentsTab from "../project/AgentsTab";
import CodeTab from "../project/CodeTab";
import TestsTab from "../project/TestsTab";
import ReviewTab from "../project/ReviewTab";
import HistoryTab from "../project/HistoryTab";
import DeliveryTab from "../project/DeliveryTab";

export interface TabProps { project: ProjectDetail; version: number; events: WorkflowEvent[]; refresh: () => void }

const TABS = [
  ["overview", "Dashboard"], ["plan", "Plan"], ["tasks", "Tasks"], ["agents", "Agents"], ["code", "Code"],
  ["tests", "Tests"], ["review", "Review"], ["history", "History"], ["delivery", "Delivery"],
] as const;
type TabId = (typeof TABS)[number][0];

const ACTIVE = new Set(["planning", "queued", "running"]);

export default function ProjectView() {
  const params = useParams();
  const id = Number(params.id);
  const tab = ((params["*"] || "overview").split("/")[0] as TabId) || "overview";
  const navigate = useNavigate();
  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [events, setEvents] = useState<WorkflowEvent[]>([]);
  const [version, setVersion] = useState(0);
  const [live, setLive] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(() => {
    api<ProjectDetail>(`/api/projects/${id}`).then((p) => { setProject(p); setError(null); }, setError);
  }, [id]);

  // Coalesce bursts of events into one refresh of the project and the open tab.
  const refresh = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => { load(); setVersion((v) => v + 1); }, 350);
  }, [load]);

  useEffect(() => {
    setEvents([]);
    load();
    const stop = streamEvents(id, {
      onEvent: (e) => { setEvents((prev) => (prev.some((p) => p.id === e.id) ? prev : [...prev, e])); refresh(); },
      onStatus: () => refresh(),
      onConnection: setLive,
    });
    return () => { stop(); window.clearTimeout(timer.current); };
  }, [id, load, refresh]);

  if (error && !project) return <main className="page"><ErrorText error={error} /><p><Link to="/">Back to projects</Link></p></main>;
  if (!project) return <main className="page"><Spinner /></main>;

  const props: TabProps = { project, version, events, refresh };
  const running = ACTIVE.has(project.status) || project.running;
  const counts: Partial<Record<TabId, number>> = {
    tasks: project.metrics.tasks_total || undefined,
    tests: project.metrics.test_runs || undefined,
  };

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <div className="small faint"><Link to="/">Projects</Link> / #{project.id}</div>
          <div className="row" style={{ marginTop: 4 }}>
            <h1>{project.name}</h1>
            <StatusBadge status={project.status} live={running} />
            <span className="badge">{label(project.mode)}</span>
            <span className={`badge ${live ? "ok" : ""}`} title="Live event stream">{live ? "Live" : "Reconnecting…"}</span>
          </div>
          {project.status_reason && <p>{project.status_reason}</p>}
        </div>
        <Controls project={project} onChange={(p) => { setProject(p); refresh(); }} onPlanTab={() => navigate(`/projects/${id}/plan`)} />
      </div>

      <nav className="tabs" aria-label="Project sections">
        {TABS.map(([key, name]) => (
          <button key={key} className={tab === key ? "on" : ""} aria-current={tab === key ? "page" : undefined}
            onClick={() => navigate(key === "overview" ? `/projects/${id}` : `/projects/${id}/${key}`)}>
            {name}{counts[key] ? <span className="count">{counts[key]}</span> : null}
          </button>
        ))}
      </nav>

      {tab === "overview" && <Overview {...props} />}
      {tab === "plan" && <PlanTab {...props} onChange={(p) => { setProject(p); refresh(); }} />}
      {tab === "tasks" && <TasksTab {...props} />}
      {tab === "agents" && <AgentsTab {...props} />}
      {tab === "code" && <CodeTab {...props} />}
      {tab === "tests" && <TestsTab {...props} />}
      {tab === "review" && <ReviewTab {...props} />}
      {tab === "history" && <HistoryTab {...props} />}
      {tab === "delivery" && <DeliveryTab {...props} />}
    </main>
  );
}
