"""LangGraph workflows (spec section 11).

Planning:   plan -> persist_plan -> END                         (then waits for human approval)
Execution:  prepare -> select_task -> develop -> test -+-> select_task (task passed)
                          |                ^           +-> debug -> test (bounded retries)
                          v                |           +-> review / finalize (revision path)
                        review -> revise --+
                          |
                          v
                       finalize -> package -> END
Baseline:   prepare -> single_build -> finalize -> package -> END

Durable state lives in the database, not the graph checkpoint: a resumed run re-enters at
`prepare`, and `select_task` skips tasks that already completed with test evidence.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select

from app.agents.schemas import PlanTask, topological_order
from app.agents.tester import report_for_model
from app.db import session_scope
from app.models import Project, ProjectStatus, ReviewFinding, Task, TaskStatus
from app.orchestrator.context import Halt, RunContext
from app.orchestrator.delivery import build_package
from app.templates import apply_template
from app.tools.analysis import run_ruff, security_scan

BLOCKING_SEVERITIES = {"critical", "high", "medium"}
UNRESOLVED_SEVERITIES = {"critical", "high"}
RECURSION_LIMIT = 400


class WorkflowState(TypedDict, total=False):
    phase: str  # task | revise
    route: str
    task_key: str | None
    failing: list[str]
    last_report: dict
    debug_history: list[dict]
    feedback: list[str]
    revise_debug_attempts: int
    checkpoint: str | None
    review_recommendation: str
    outcome: str
    reason: str


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------------------------

class PlanningWorkflow:
    def __init__(self, ctx: RunContext, feedback: str | None = None):
        self.ctx = ctx
        self.feedback = feedback
        g = StateGraph(dict)
        g.add_node("plan", self.plan)
        g.add_node("persist_plan", self.persist_plan)
        g.add_edge(START, "plan")
        g.add_edge("plan", "persist_plan")
        g.add_edge("persist_plan", END)
        self.graph = g.compile()

    def run(self) -> dict:
        return self.graph.invoke({}, {"recursion_limit": RECURSION_LIMIT})

    def plan(self, state: dict) -> dict:
        ctx = self.ctx
        ctx.recorder.event("phase", "Analysing requirements and generating a plan", agent="planner")
        plan = ctx.planner.plan(
            name=ctx.name, requirements=ctx.requirements, template=ctx.template, constraints=ctx.constraints,
            previous_plan=ctx.plan, feedback=self.feedback, meta=ctx.meta,
        )
        return {"plan": plan.model_dump()}

    def persist_plan(self, state: dict) -> dict:
        plan = state["plan"]
        tasks = [PlanTask.model_validate(t) for t in plan["tasks"]]
        order = {key: i for i, key in enumerate(topological_order(tasks) or [])}
        with session_scope() as db:
            project = db.get(Project, self.ctx.project_id)
            for task in list(project.tasks):
                db.delete(task)
            db.flush()
            for t in tasks:
                db.add(Task(
                    project_id=project.id, key=t.id, title=t.title, description=t.description, depends_on=t.depends_on,
                    expected_outputs=t.expected_outputs, acceptance_criteria=t.acceptance_criteria, order_index=order.get(t.id, 0),
                ))
            project.plan = plan
            project.plan_version += 1
            project.status = ProjectStatus.AWAITING_APPROVAL
            project.status_reason = (
                f"{len(plan['clarifications_needed'])} clarification question(s) recorded; answer them by requesting a revision"
                if plan["clarifications_needed"] else "Plan ready for review"
            )
            version = project.plan_version
        self.ctx.recorder.event("plan_ready", f"Plan v{version} with {len(tasks)} tasks awaiting approval", agent="planner",
                                data={"plan_version": version, "tasks": [t.id for t in tasks]})
        return {}


# --------------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------------

class ExecutionWorkflow:
    def __init__(self, ctx: RunContext):
        self.ctx = ctx
        g = StateGraph(WorkflowState)
        node = self._node
        if ctx.mode == "single_agent":
            g.add_node("prepare", node(self.prepare))
            g.add_node("single_build", node(self.single_build))
            g.add_node("finalize", node(self.finalize))
            g.add_node("package", node(self.package))
            g.add_edge(START, "prepare")
            g.add_edge("prepare", "single_build")
            g.add_edge("single_build", "finalize")
        else:
            for name in ("prepare", "select_task", "develop", "test", "debug", "review", "revise", "finalize", "package"):
                g.add_node(name, node(getattr(self, name)))
            g.add_edge(START, "prepare")
            g.add_edge("prepare", "select_task")
            g.add_conditional_edges("select_task", _route, ["develop", "review"])
            g.add_edge("develop", "test")
            g.add_conditional_edges("test", _route, ["select_task", "debug", "review", "finalize"])
            g.add_edge("debug", "test")
            g.add_conditional_edges("review", _route, ["revise", "finalize"])
            g.add_edge("revise", "test")
        g.add_edge("finalize", "package")
        g.add_edge("package", END)
        self.graph = g.compile()

    def run(self) -> WorkflowState:
        return self.graph.invoke({"phase": "task"}, {"recursion_limit": RECURSION_LIMIT})

    def _node(self, fn: Callable[[WorkflowState], dict]) -> Callable[[WorkflowState], dict]:
        def wrapped(state: WorkflowState) -> dict:
            self.ctx.check_control()
            return fn(state)

        wrapped.__name__ = fn.__name__
        return wrapped

    # --- nodes -------------------------------------------------------------------------

    def prepare(self, state: WorkflowState) -> dict:
        ctx = self.ctx
        ctx.workspace.ensure()
        if not (ctx.workspace.root / ".git").exists():
            written = apply_template(ctx.template, ctx.workspace)
            ctx.git.init()
            ctx.git.commit_all(f"Initialize from template {ctx.template.id} v{ctx.template.version}")
            ctx.recorder.event("workspace_ready", f"Workspace initialised from {ctx.template.name} ({len(written)} files)")
        with session_scope() as db:
            resumed = 0
            for task in db.scalars(select(Task).where(Task.project_id == ctx.project_id)):
                if task.status in (TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.BLOCKED):
                    task.status = TaskStatus.PENDING
                    task.debug_attempts = 0
                    resumed += 1
        if resumed:
            ctx.recorder.event("workflow_resumed", f"Resuming; {resumed} unfinished task(s) reset to pending")
        return {"phase": "task", "debug_history": [], "feedback": []}

    def select_task(self, state: WorkflowState) -> dict:
        with session_scope() as db:
            tasks = list(db.scalars(select(Task).where(Task.project_id == self.ctx.project_id).order_by(Task.order_index, Task.id)))
            done = {t.key for t in tasks if t.status == TaskStatus.COMPLETED}
            pending = [t for t in tasks if t.status == TaskStatus.PENDING]
            ready = next((t for t in pending if set(t.depends_on) <= done), None)
            if ready:
                ready.status = TaskStatus.RUNNING
                ready.attempts += 1
                ready.started_at = _now()
                key, title = ready.key, ready.title
        if ready:
            self.ctx.recorder.event("task_started", f"{key}: {title}", data={"task": key})
            return {"route": "develop", "task_key": key, "phase": "task", "debug_history": [], "failing": [], "feedback": []}
        if pending:
            raise Halt(ProjectStatus.BLOCKED, f"Tasks {', '.join(t.key for t in pending)} cannot start: dependencies are not complete")
        self.ctx.recorder.event("phase", "All tasks complete; starting review")
        return {"route": "review", "task_key": None}

    def develop(self, state: WorkflowState) -> dict:
        ctx = self.ctx
        task = self._task(state["task_key"])
        with session_scope() as db:
            completed = [f"{t.key}: {t.title}" for t in db.scalars(select(Task).where(
                Task.project_id == ctx.project_id, Task.status == TaskStatus.COMPLETED).order_by(Task.order_index))]
        out = ctx.developer.implement(
            plan=ctx.plan, task=task, template=ctx.template, files=ctx.workspace.snapshot(), completed=completed,
            feedback=state.get("feedback", []), meta={**ctx.meta, "task_key": task["id"]}, task_id=task["db_id"],
        )
        _, rejected = ctx.apply_changes(out.files, task_id=task["db_id"], message=f"{task['id']}: {out.summary}")
        return {"feedback": rejected}

    def test(self, state: WorkflowState) -> dict:
        ctx = self.ctx
        phase = state.get("phase", "task")
        task = self._task(state["task_key"]) if phase == "task" else None
        task_id = task["db_id"] if task else None
        failing = state.get("failing") or []

        report = None
        if failing:
            # FR-11: rerun the previously failing tests first, then the full regression suite.
            report, _ = ctx.tester.run(ctx.workspace, kind="retest", task_id=task_id, selectors=failing)
        if report is None or report.success:
            kind = "regression" if failing else ("revise" if phase == "revise" else "task")
            report, run_id = ctx.tester.run(ctx.workspace, kind=kind, task_id=task_id)

        if report.success:
            if phase == "task":
                with session_scope() as db:
                    row = db.get(Task, task_id)
                    row.status = TaskStatus.COMPLETED
                    row.finished_at = _now()
                    row.evidence = {"test_run_id": run_id, "summary": report.summary, "commit": ctx.git.head(),
                                    "debug_attempts": row.debug_attempts, "no_tests": report.no_tests}
                ctx.recorder.event("task_completed", f"{task['id']} completed: {report.summary}", data={"task": task["id"]})
                return {"route": "select_task", "failing": []}
            ctx.recorder.event("revision_validated", f"Review revision passed tests: {report.summary}")
            return {"route": "review", "failing": []}

        model_report = report_for_model(report)
        attempts = self._debug_attempts(task_id, state)
        if attempts < ctx.limits.max_debug_attempts:
            return {"route": "debug", "failing": report.failing_nodeids(), "last_report": model_report}

        if phase == "task":
            with session_scope() as db:
                row = db.get(Task, task_id)
                row.status = TaskStatus.FAILED
                row.finished_at = _now()
                row.evidence = {"last_failure": model_report["summary"], "debug_attempts": attempts}
            raise Halt(ProjectStatus.BLOCKED,
                       f"{task['id']} still failing after {attempts} debug attempt(s): {report.summary}. "
                       "Review the failure, then resume to retry or cancel.")

        checkpoint = state.get("checkpoint")
        if checkpoint:
            ctx.git.reset_hard(checkpoint)
        ctx.recorder.event("revision_rolled_back", f"Review revision could not be validated ({report.summary}); "
                           f"rolled back to {checkpoint[:8] if checkpoint else 'previous state'}")
        return {"route": "finalize", "failing": []}

    def debug(self, state: WorkflowState) -> dict:
        ctx = self.ctx
        phase = state.get("phase", "task")
        task = self._task(state["task_key"]) if phase == "task" else None
        task_id = task["db_id"] if task else None
        updates: dict[str, Any] = {}
        if task:
            with session_scope() as db:
                row = db.get(Task, task_id)
                row.debug_attempts += 1
                attempt = row.debug_attempts
        else:
            attempt = state.get("revise_debug_attempts", 0) + 1
            updates["revise_debug_attempts"] = attempt
        ctx.recorder.event("debug_attempt", f"Debug attempt {attempt}/{ctx.limits.max_debug_attempts}",
                           agent="debugger", data={"task": task["id"] if task else None, "attempt": attempt})
        out = ctx.debugger.diagnose(
            task=task and {k: v for k, v in task.items() if k != "db_id"}, template=ctx.template,
            files=ctx.workspace.snapshot(), report=state.get("last_report", {}), previous=state.get("debug_history", []),
            meta={**ctx.meta, "task_key": task["id"] if task else None, "attempt": attempt}, task_id=task_id,
        )
        label = task["id"] if task else "revision"
        ctx.apply_changes(out.files, task_id=task_id, message=f"{label} fix: {out.root_cause[:120]}")
        history = state.get("debug_history", []) + [{"attempt": attempt, "root_cause": out.root_cause, "files": [f.path for f in out.files]}]
        return {**updates, "debug_history": history}

    def review(self, state: WorkflowState) -> dict:
        ctx = self.ctx
        with session_scope() as db:
            project = db.get(Project, ctx.project_id)
            round_ = project.review_round
            previous = [_finding_dict(f) for f in db.scalars(select(ReviewFinding).where(
                ReviewFinding.project_id == ctx.project_id, ReviewFinding.status == "open"))]
            for f in db.scalars(select(ReviewFinding).where(ReviewFinding.project_id == ctx.project_id, ReviewFinding.status == "open")):
                f.status = "superseded"

        issues, ruff_error = run_ruff(ctx.workspace)
        issues += security_scan(ctx.workspace)
        analysis = [i.as_dict() for i in issues]
        if ruff_error:
            ctx.recorder.event("analysis_warning", ruff_error)
        last_tests = self._latest_test_summary()

        report = ctx.reviewer.review(
            plan=ctx.plan, template=ctx.template, files=ctx.workspace.snapshot(), test_summary=last_tests,
            analysis=analysis, previous_findings=previous, meta={**ctx.meta, "review_round": round_},
        )
        with session_scope() as db:
            for f in report.findings:
                db.add(ReviewFinding(project_id=ctx.project_id, round=round_, source="reviewer", severity=f.severity, category=f.category,
                                     file=f.file, line=f.line, description=f.description, recommendation=f.recommendation, actionable=f.actionable))
            for i in issues:
                if i.severity in ("high", "medium"):
                    db.add(ReviewFinding(project_id=ctx.project_id, round=round_, source=i.source, severity=i.severity,
                                         category="security" if i.source == "security" or i.code.startswith("S") else "correctness",
                                         file=i.file, line=i.line, description=f"[{i.code}] {i.message}",
                                         recommendation="Address the reported issue", actionable=True))

        blocking = [f.model_dump() for f in report.findings if f.actionable and f.severity in BLOCKING_SEVERITIES]
        blocking += [{"severity": i.severity, "category": "security", "file": i.file, "line": i.line,
                      "description": f"[{i.code}] {i.message}", "recommendation": "Fix the static-analysis finding", "actionable": True}
                     for i in issues if i.severity == "high"]
        ctx.recorder.event("review_completed", f"Review round {round_ + 1}: {report.recommendation}, {len(report.findings)} finding(s), "
                           f"{len(blocking)} actionable", agent="reviewer",
                           data={"recommendation": report.recommendation, "round": round_ + 1, "blocking": len(blocking)})

        if blocking and round_ < ctx.limits.max_review_rounds:
            return {"route": "revise", "review_recommendation": report.recommendation, "last_report": {"findings": blocking}}
        if blocking:
            ctx.recorder.event("review_limit", f"Review round limit ({ctx.limits.max_review_rounds}) reached with {len(blocking)} actionable finding(s) open")
        return {"route": "finalize", "review_recommendation": report.recommendation}

    def revise(self, state: WorkflowState) -> dict:
        ctx = self.ctx
        findings = state.get("last_report", {}).get("findings", [])
        checkpoint = ctx.git.head()
        with session_scope() as db:
            project = db.get(Project, ctx.project_id)
            project.review_round += 1
            round_ = project.review_round
        ctx.recorder.event("revision_started", f"Routing {len(findings)} review finding(s) to the developer (round {round_})")
        out = ctx.developer.revise(plan=ctx.plan, template=ctx.template, files=ctx.workspace.snapshot(), findings=findings, meta=ctx.meta)
        ctx.apply_changes(out.files, task_id=None, message=f"Review revision {round_}: {out.summary}")
        return {"phase": "revise", "checkpoint": checkpoint, "failing": [], "debug_history": [], "revise_debug_attempts": 0}

    def single_build(self, state: WorkflowState) -> dict:
        ctx = self.ctx
        out = ctx.single.build(name=ctx.name, requirements=ctx.requirements, template=ctx.template,
                               files=ctx.workspace.snapshot(), meta=ctx.meta)
        ctx.apply_changes(out.files, task_id=None, message=f"Single-agent build: {out.summary}")
        return {}

    def finalize(self, state: WorkflowState) -> dict:
        ctx = self.ctx
        ctx.recorder.event("phase", "Final validation")
        issues, _ = run_ruff(ctx.workspace)
        issues += security_scan(ctx.workspace)
        checks = {
            "static_issues": [i.as_dict() for i in issues if i.source == "ruff"],
            "security_issues": [i.as_dict() for i in issues if i.source == "security"],
        }
        report, run_id = ctx.tester.run(ctx.workspace, kind="final", checks=checks)

        with session_scope() as db:
            tasks = list(db.scalars(select(Task).where(Task.project_id == ctx.project_id)))
            open_findings = list(db.scalars(select(ReviewFinding).where(
                ReviewFinding.project_id == ctx.project_id, ReviewFinding.status == "open")))
            unresolved = [f for f in open_findings if f.severity in UNRESOLVED_SEVERITIES]
            # Findings from earlier rounds that the latest review no longer raises count as resolved.
            for f in db.scalars(select(ReviewFinding).where(ReviewFinding.project_id == ctx.project_id, ReviewFinding.status == "superseded")):
                f.status = "resolved"

        incomplete = [t.key for t in tasks if t.status != TaskStatus.COMPLETED] if ctx.mode != "single_agent" else []
        high_security = [i for i in issues if i.source == "security" and i.severity == "high"]
        reasons = []
        if not report.success:
            outcome = ProjectStatus.FAILED
            reasons.append(f"final test run failed: {report.summary}")
        else:
            if report.total == 0:
                reasons.append("no tests were collected, so behaviour is unverified")
            if incomplete:
                reasons.append(f"incomplete tasks: {', '.join(incomplete)}")
            if unresolved:
                reasons.append(f"{len(unresolved)} unresolved critical/high review finding(s)")
            if high_security:
                reasons.append(f"{len(high_security)} high-severity security issue(s)")
            if state.get("review_recommendation") == "human_review":
                reasons.append("reviewer requested human review")
            outcome = ProjectStatus.PARTIAL if reasons else ProjectStatus.COMPLETED
        reason = "; ".join(reasons) or f"All tasks complete, {report.summary}, no unresolved blocking findings"
        ctx.recorder.event("final_validation", f"{outcome}: {reason}", data={"test_run_id": run_id, "outcome": outcome})
        return {"outcome": outcome, "reason": reason}

    def package(self, state: WorkflowState) -> dict:
        path = build_package(self.ctx, outcome=state["outcome"], reason=state["reason"])
        self.ctx.recorder.event("delivered", f"Delivery package created ({path.name})", data={"package": path.name})
        return {}

    # --- helpers -----------------------------------------------------------------------

    def _task(self, key: str) -> dict:
        with session_scope() as db:
            t = db.scalar(select(Task).where(Task.project_id == self.ctx.project_id, Task.key == key))
            return {"db_id": t.id, "id": t.key, "title": t.title, "description": t.description, "depends_on": t.depends_on,
                    "expected_outputs": t.expected_outputs, "acceptance_criteria": t.acceptance_criteria}

    def _debug_attempts(self, task_id: int | None, state: WorkflowState) -> int:
        if task_id is None:
            return state.get("revise_debug_attempts", 0)
        with session_scope() as db:
            return db.get(Task, task_id).debug_attempts

    def _latest_test_summary(self) -> dict:
        from app.models import TestRun

        with session_scope() as db:
            run = db.scalar(select(TestRun).where(TestRun.project_id == self.ctx.project_id).order_by(TestRun.id.desc()))
            if run is None:
                return {"summary": "no test runs"}
            return {"summary": run.summary, "passed": run.passed, "failed": run.failed, "errors": run.errors, "kind": run.kind}


def _route(state: WorkflowState) -> str:
    return state["route"]


def _finding_dict(f: ReviewFinding) -> dict:
    return {"severity": f.severity, "category": f.category, "file": f.file, "line": f.line,
            "description": f.description, "recommendation": f.recommendation}
