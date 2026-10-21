import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useSession } from "../App";
import type { ProjectDetail, Template } from "../types";
import { ErrorText, useLoad } from "../components/ui";

const EXAMPLE = `Build a REST API for a student attendance system.
- Admins manage students (name, roll number, class) and courses.
- Teachers record attendance per course session (present / absent / late).
- Report endpoint: attendance percentage per student per course, and a list of students below 75%.
- Use SQLite through SQLAlchemy. Validate inputs and return 404 for unknown ids.`;

export default function NewProject() {
  const navigate = useNavigate();
  const { system } = useSession();
  const templates = useLoad(() => api<Template[]>("/api/templates"), []);
  const [name, setName] = useState("");
  const [requirements, setRequirements] = useState("");
  const [template, setTemplate] = useState("fastapi_service");
  const [mode, setMode] = useState<"multi_agent" | "single_agent">("multi_agent");
  const [debug, setDebug] = useState(system?.limits.max_debug_attempts ?? 3);
  const [rounds, setRounds] = useState(system?.limits.max_review_rounds ?? 1);
  const [budget, setBudget] = useState(system?.limits.max_cost_usd ?? 5);
  const [notes, setNotes] = useState("");
  const [scenario, setScenario] = useState("default");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const selected = templates.data?.find((t) => t.id === template);
  const mock = system?.llm_provider === "mock";

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const constraints: Record<string, unknown> = { max_debug_attempts: debug, max_review_rounds: rounds, max_cost_usd: budget };
      if (notes.trim()) constraints.notes = notes.trim();
      if (mock) constraints.mock_scenario = scenario;
      const project = await api<ProjectDetail>("/api/projects", { body: { name, requirements, template, mode, constraints } });
      if (mode === "multi_agent") await api(`/api/projects/${project.id}/plan`, { method: "POST" });
      else await api(`/api/projects/${project.id}/start`, { method: "POST" });
      navigate(`/projects/${project.id}`);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <main className="page" style={{ maxWidth: 860 }}>
      <div className="page-head">
        <div>
          <h1>New project</h1>
          <p>The planner turns this into a specification and task plan for you to approve before any code runs.</p>
        </div>
      </div>
      <form className="card card-body" onSubmit={submit}>
        <div className="field">
          <label htmlFor="name">Project name</label>
          <input id="name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={200} placeholder="Student attendance API" />
        </div>
        <div className="field">
          <label htmlFor="req">Requirements</label>
          <textarea id="req" value={requirements} onChange={(e) => setRequirements(e.target.value)} required minLength={20}
            maxLength={20000} rows={9} placeholder={EXAMPLE} />
          <div className="hint row" style={{ justifyContent: "space-between" }}>
            <span>Describe behaviour, data, endpoints and rules. Missing details are recorded as assumptions.</span>
            <a href="#" onClick={(e) => { e.preventDefault(); setRequirements(EXAMPLE); if (!name) setName("Student attendance API"); }}>Use example</a>
          </div>
        </div>

        <div className="field field-row">
          <div>
            <label htmlFor="tpl">Template</label>
            <select id="tpl" value={template} onChange={(e) => setTemplate(e.target.value)}>
              {templates.data?.map((t) => <option key={t.id} value={t.id}>{t.name} (v{t.version})</option>)}
            </select>
          </div>
          <div>
            <label>Workflow</label>
            <div className="segmented" role="group" aria-label="Workflow mode">
              <button type="button" className={mode === "multi_agent" ? "on" : ""} onClick={() => setMode("multi_agent")}>Multi-agent</button>
              <button type="button" className={mode === "single_agent" ? "on" : ""} onClick={() => setMode("single_agent")}>Single-agent baseline</button>
            </div>
          </div>
        </div>
        {selected && (
          <p className="hint">{selected.description} Approved dependencies: <span className="mono">{selected.approved_dependencies.join(", ")}</span></p>
        )}
        {mode === "single_agent" && (
          <div className="banner info field small">The baseline generates the whole project in one model call with no planning, debugging or review. Use it to compare against the multi-agent workflow.</div>
        )}

        <div className="field">
          <div className="section-title" style={{ marginTop: 8 }}>Execution limits</div>
          <div className="field-row">
            <div>
              <label htmlFor="dbg">Max debug attempts per task</label>
              <input id="dbg" type="number" min={0} max={10} value={debug} onChange={(e) => setDebug(Number(e.target.value))} />
            </div>
            <div>
              <label htmlFor="rr">Review revision rounds</label>
              <input id="rr" type="number" min={0} max={5} value={rounds} onChange={(e) => setRounds(Number(e.target.value))} />
            </div>
            <div>
              <label htmlFor="bud">Model budget (USD)</label>
              <input id="bud" type="number" min={0.01} step={0.01} max={system?.limits.max_cost_usd} value={budget} onChange={(e) => setBudget(Number(e.target.value))} />
            </div>
          </div>
        </div>
        <div className="field">
          <label htmlFor="notes">Additional constraints <span className="faint">(optional)</span></label>
          <input id="notes" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={4000} placeholder="e.g. Use integer ids; timestamps in UTC" />
        </div>
        {mock && (
          <div className="field">
            <label htmlFor="scn">Mock scenario</label>
            <select id="scn" value={scenario} onChange={(e) => setScenario(e.target.value)}>
              <option value="default">Default: bug detected, debugged, review revision, delivered</option>
              <option value="clean">Clean: no failures</option>
              <option value="unfixable">Unfixable: retries exhausted, workflow blocks</option>
              <option value="unsafe">Unsafe: developer attempts a path traversal</option>
            </select>
            <div className="hint">The mock provider ignores your requirements and scripts a Notes API. Set DEVFLOW_LLM_PROVIDER=anthropic for real generation.</div>
          </div>
        )}
        <div className="field"><ErrorText error={error} /></div>
        <div className="field row" style={{ justifyContent: "flex-end" }}>
          <button type="button" onClick={() => navigate("/")}>Cancel</button>
          <button className="primary" type="submit" disabled={busy}>{busy ? "Creating…" : mode === "multi_agent" ? "Create and generate plan" : "Create and run baseline"}</button>
        </div>
      </form>
    </main>
  );
}
