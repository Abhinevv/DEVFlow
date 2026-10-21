# DevFlow AI

An autonomous multi-agent software engineering system. You describe a service in plain language; a **Planner**, **Developer**, **Tester**, **Debugger** and **Reviewer** agent turn it into a planned, implemented, tested, reviewed and packaged project. A LangGraph orchestrator coordinates them, and every completion decision rests on real execution results rather than an agent saying it succeeded.

```
React dashboard ──► FastAPI (auth, projects, SSE) ──► LangGraph orchestrator ──► agents ──► LLM (Claude API | mock)
                                │                              │
                                ▼                              ▼
                     SQLite / PostgreSQL            workspace guard · Git · Docker sandbox · pytest · Ruff · security scan
```

## Quick start (Windows, PowerShell)

Requires Python 3.11+, Node 18+, and Git. Docker is optional but **required for isolated execution of model-generated code**.

```powershell
# Backend
cd backend
py -m venv .venv
.venv\Scripts\python -m pip install -e .
copy .env.example .env          # then edit; see Configuration
.venv\Scripts\python -m uvicorn app.main:app --port 8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev                     # http://localhost:5173 (proxies /api to :8000)
```

On macOS/Linux use `python3 -m venv .venv` and `.venv/bin/python`.

Register an account in the UI (the first account becomes admin), create a project, review and approve the plan, and watch the agents work. The dashboard updates live over Server-Sent Events.

### Running without an API key

The default provider is `mock`: a deterministic script that ignores your requirements and builds a small Notes API. Its default scenario **deliberately ships a bug** so a demo shows the whole loop: failing test → debugger fix → failed test rerun → regression suite → review finding → revision → final validation → package. Other scenarios (selectable on the New Project form): `clean`, `unfixable` (retries exhausted, workflow blocks), `unsafe` (developer attempts a path traversal and gets rejected).

### Using Claude

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."      # or: ant auth login
# in backend/.env
DEVFLOW_LLM_PROVIDER=anthropic
DEVFLOW_LLM_MODEL=claude-opus-5-5
DEVFLOW_ALLOW_LOCAL_EXECUTION=false
```

Build the sandbox image and start Docker before running model-generated code:

```powershell
docker build -t devflow-sandbox:latest sandbox
```

Each agent makes one streamed Messages API call with a JSON-schema structured output (`output_config.format`), validated against Pydantic models, with one corrective retry on validation failure. Prompt caching and server-side refusal fallbacks are enabled. Token usage and estimated cost are recorded per call and enforced against the project budget.

## Configuration

All settings are environment variables prefixed `DEVFLOW_` (or `backend/.env`). See [backend/.env.example](backend/.env.example).

| Setting | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `anthropic` or `mock` |
| `LLM_MODEL` / `LLM_EFFORT` | `claude-opus-5-5` / `high` | Model and effort for every agent |
| `EXECUTOR` | `auto` | `docker`, `local`, or `auto` (Docker if reachable) |
| `ALLOW_LOCAL_EXECUTION` | `false` | Permit the non-isolated local executor when Docker is unavailable |
| `MAX_DEBUG_ATTEMPTS` | `3` | Debug retries per failing task (projects may lower; hard cap 10) |
| `MAX_REVIEW_ROUNDS` | `1` | Review → revise cycles (hard cap 5) |
| `MAX_COST_USD` | `5.0` | Per-project model spend ceiling |
| `MAX_WORKFLOW_SECONDS` | `3600` | Per-project active-time ceiling |
| `EXEC_TIMEOUT_SECONDS` / `EXEC_MEMORY_MB` | `120` / `512` | Sandbox limits per test run |
| `DATABASE_URL` | SQLite in `backend/data` | e.g. `postgresql+psycopg://…` (`pip install -e .[postgres]`, `docker compose up -d postgres`) |

## How the workflow runs

**Planning** (`plan → persist_plan`): the Planner extracts functional and non-functional requirements, assumptions, constraints, clarification questions and acceptance criteria, then a task graph. The plan is rejected and re-requested if it has unknown dependencies or cycles. The project then waits in `awaiting_approval`; nothing executes until a human approves. Rejecting or requesting changes is also supported, and revision feedback goes back to the Planner.

**Execution** ([backend/app/orchestrator/graph.py](backend/app/orchestrator/graph.py)):

1. `prepare`: initialise the workspace from the template and Git, and reset unfinished tasks when resuming.
2. `select_task`: pick the next task whose dependencies are all complete.
3. `develop`: the Developer returns complete files. Every path passes the workspace policy and requirements are checked against the dependency allow-list. Each change set is committed.
4. `test`: the Tester runs a syntax check, then pytest in the sandbox, and parses JUnit XML. On success the task completes with evidence (test run id, commit).
5. `debug` (on failure, bounded): the Debugger gets the real failures and previous hypotheses. Afterwards the **failed tests are rerun first, then the full regression suite** (FR-11). When attempts run out, the task fails and the workflow **blocks** for a human, who can resume or cancel.
6. `review`: Ruff, an AST security scan, and the Reviewer. Actionable critical/high/medium findings route to `revise` (the Developer), which is validated like any change. If the revision can't be made to pass, it is **rolled back via Git**.
7. `finalize`: a full test run plus static and security checks. Status is `completed` only if all tasks are done, tests pass with more than 0 collected, and no critical/high findings or high-severity security issues remain. Otherwise `partial` or `failed`, with the reason.
8. `package`: a zip with `source/`, `DEVFLOW_REPORT.md` (setup, tests, findings, limitations) and `devflow_manifest.json` (config snapshot for reproducibility).

Pause and cancel take effect at the next step boundary. Resume re-enters at `prepare` and skips tasks that already completed with test evidence. Workflows interrupted by a server restart are marked resumable on startup.

**Single-agent baseline** (`mode=single_agent`): one model call generates the whole project, then the same final validation runs. There is no planning, debugging or review. It exists for comparison.

## Security model

Generated code and tool arguments are treated as untrusted.

- **Workspace guard** ([workspace.py](backend/app/tools/workspace.py)): rejects absolute paths, drive letters, `..`, `.` and empty segments, backslashes, hidden paths (`.git`, `.env`), Windows reserved names, symlinks, disallowed file types, oversized files and too many files. Rejections are recorded as safety violations, and the workflow stops after the configured limit.
- **Dependency policy**: `requirements.txt` may only name the template's approved packages; options, URLs and local paths are refused.
- **Docker executor**: `--network none`, read-only root and workspace mounts, tmpfs `/tmp`, memory/CPU/PID limits, `--cap-drop ALL`, `no-new-privileges`, uid 65534, and a hard timeout with kill. Test output leaves through stdout, so generated code cannot alter the workspace.
- **Local executor**: runs on a throwaway copy with a scrubbed environment (no API keys or `DEVFLOW_*` values), `python -I`, a timeout with process-tree kill, and rlimits on POSIX. **It is not a security boundary**, is off by default, and the UI and run history flag every non-isolated run. Use it only with the mock provider or code you trust.
- **Auth**: scrypt password hashes and HMAC-signed expiring bearer tokens. Every project route checks ownership and returns 404 for projects you don't own. SSE uses a header token, not a URL parameter.
- Static analysis runs on the host only as parsing (Ruff/AST) and never executes generated code.

Containers alone do not make arbitrary untrusted code safe. For shared or production deployments, add host-level isolation such as gVisor or a dedicated VM.

## Integrations (n8n and other automation)

**Integrations** in the dashboard header manages two things:

- **Webhooks:** DevFlow POSTs signed JSON events (`plan_ready`, `workflow_halted`, `delivered`, …) to URLs you configure. Each webhook has an event filter and optional auth headers. Deliveries are retried and logged.
- **API keys:** long-lived `dfk_…` keys that automation tools use as `Authorization: Bearer dfk_…`. Only a hash is stored, and keys can be revoked.

[integrations/n8n](integrations/n8n) has ready-to-import n8n workflows: Telegram notifications with a signed plan-approval link, approval through that link, and a form that creates projects. It also has the setup guide. `docker compose up -d n8n` starts n8n at http://localhost:5678.

## Evaluation

```powershell
cd backend
.venv\Scripts\python -m evaluation.run_benchmark --repeats 3
```

The three benchmark tasks in [backend/evaluation/benchmark](backend/evaluation/benchmark) have hidden acceptance tests the agents never see. They run in the same sandbox against each final workspace. Output: `evaluation/results/<timestamp>/report.md` and `results.json`, with task completion rate, acceptance and internal test pass rates, recovery rate, retries, duration, cost, cost per success, human intervention rate and safety violations, for each mode.

With the mock provider only `notes_api` is meaningful; it produces multi-agent 6/6 vs baseline 4/6 by construction. **Real numbers require the Anthropic provider**, and per spec §18, targets should be set after measuring a baseline.

## Tests

```powershell
cd backend; .venv\Scripts\python -m pytest       # 61 tests: workspace policy, sandbox, parsing, security scan, auth, webhooks, full workflows
cd frontend; npm run build                      # type-check and production build
```

The workflow tests drive the real API, LangGraph graph, executor and pytest through every mock scenario: success with recovery, retry exhaustion → blocked → cancel, path traversal rejection, single-agent baseline, pause/resume without re-running completed tasks, and plan revise/reject..

## Requirements traceability

| Requirement | Implementation |
|---|---|
| FR-01 Project creation | `POST /api/projects`, [NewProject.tsx](frontend/src/pages/NewProject.tsx) |
| FR-02/03 Analysis & plan | [PlannerAgent](backend/app/agents/roles.py), [Plan schema + DAG validation](backend/app/agents/schemas.py) |
| FR-04 Plan approval | `/plan/approve`, `/plan/reject`, `/plan/revise`, [PlanTab.tsx](frontend/src/project/PlanTab.tsx) |
| FR-05 Task management | `Task` model, `select_task` dependency gating, [TasksTab.tsx](frontend/src/project/TasksTab.tsx) |
| FR-06/07 Code generation, controlled files | [DeveloperAgent](backend/app/agents/roles.py), [RunContext.apply_changes](backend/app/orchestrator/context.py), [Workspace](backend/app/tools/workspace.py) |
| FR-08 Automated testing | [TesterAgent](backend/app/agents/tester.py), [testing.py](backend/app/tools/testing.py) |
| FR-09/10/11 Debugging, bounded recovery, regression | `debug`/`test` nodes in [graph.py](backend/app/orchestrator/graph.py) |
| FR-12/13 Review & routing | [ReviewerAgent](backend/app/agents/roles.py), `review`/`revise` nodes, [analysis.py](backend/app/tools/analysis.py) |
| FR-14 Monitoring | SSE `/events/stream`, [Overview.tsx](frontend/src/project/Overview.tsx), [AgentsTab.tsx](frontend/src/project/AgentsTab.tsx) |
| FR-15 Persistent history | [models.py](backend/app/models.py) (events, agent runs, test runs, artifacts, approvals, usage), Git per workspace |
| FR-16 Delivery | [delivery.py](backend/app/orchestrator/delivery.py), [DeliveryTab.tsx](frontend/src/project/DeliveryTab.tsx) |
| FR-17 Human intervention | `/pause`, `/resume`, `/cancel`, [Controls.tsx](frontend/src/project/Controls.tsx), [runner.py](backend/app/orchestrator/runner.py) |
| FR-18 Evaluation | [metrics.py](backend/app/metrics.py), [run_benchmark.py](backend/evaluation/run_benchmark.py) |

## Known limitations

- One template (FastAPI + SQLAlchemy + pytest). React and full-stack templates are future work (spec §6.2).
- Tasks run sequentially. Parallel execution of independent tasks is not implemented.
- The Developer and Debugger return whole files. That makes changes simple to validate but costly for large files.
- Durable state lives in the database rather than LangGraph checkpoints. Resume restarts the current task's development step, not the exact node.
- Workflows run in an in-process thread pool. A Redis-backed queue (spec §12.3) is the next step for multiple workers.
- The local executor has no network or resource isolation on Windows.
