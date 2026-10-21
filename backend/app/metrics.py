"""Evaluation metrics (FR-18, spec section 18), computed from persisted records."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AgentRun, Approval, Project, ProjectStatus, Task, TaskStatus, TestRun, UsageRecord, WorkflowEvent

INTERVENTION_KINDS = {"resume", "pause", "cancel"}


def project_metrics(db: Session, project: Project) -> dict:
    pid = project.id
    tasks = list(db.scalars(select(Task).where(Task.project_id == pid)))
    final = db.scalar(select(TestRun).where(TestRun.project_id == pid, TestRun.kind == "final").order_by(TestRun.id.desc()))
    usage = db.execute(select(
        func.coalesce(func.sum(UsageRecord.cost_usd), 0.0),
        func.coalesce(func.sum(UsageRecord.input_tokens + UsageRecord.cache_read_tokens + UsageRecord.cache_write_tokens), 0),
        func.coalesce(func.sum(UsageRecord.output_tokens), 0),
        func.count(UsageRecord.id),
    ).where(UsageRecord.project_id == pid)).one()
    debug_runs = db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.project_id == pid, AgentRun.role == "debugger"))
    approvals = list(db.scalars(select(Approval).where(Approval.project_id == pid)))
    interventions = sum(1 for a in approvals if a.kind in INTERVENTION_KINDS or (a.kind == "plan" and a.decision != "approved"))
    violations = db.scalar(select(func.count()).select_from(WorkflowEvent).where(
        WorkflowEvent.project_id == pid, WorkflowEvent.type == "safety_violation"))
    test_runs = db.scalar(select(func.count()).select_from(TestRun).where(TestRun.project_id == pid))

    # A task that needed debugging is a recovery opportunity; it recovered if it then completed with passing tests.
    eligible = [t for t in tasks if t.debug_attempts > 0]
    recovered = [t for t in eligible if t.status == TaskStatus.COMPLETED]

    required = (final.total - final.skipped) if final else 0
    return {
        "status": project.status,
        "completed": project.status == ProjectStatus.COMPLETED,
        "tasks_total": len(tasks),
        "tasks_completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
        "test_pass_rate": round(final.passed / required, 4) if final and required else None,
        "final_tests": None if final is None else {"passed": final.passed, "failed": final.failed, "errors": final.errors, "total": final.total},
        "test_runs": test_runs,
        "retry_count": debug_runs,
        "recovery_eligible": len(eligible),
        "recovered": len(recovered),
        "recovery_rate": round(len(recovered) / len(eligible), 4) if eligible else None,
        "duration_seconds": round(project.active_seconds or 0.0, 2),
        "cost_usd": round(float(usage[0]), 6),
        "input_tokens": int(usage[1]),
        "output_tokens": int(usage[2]),
        "model_calls": int(usage[3]),
        "human_interventions": interventions,
        "safety_violations": violations,
    }


def aggregate_metrics(db: Session, projects: list[Project]) -> dict:
    terminal = [p for p in projects if p.status in ProjectStatus.TERMINAL]
    per = [project_metrics(db, p) for p in terminal]
    completed = [m for m in per if m["completed"]]
    pass_rates = [m["test_pass_rate"] for m in per if m["test_pass_rate"] is not None]
    eligible = sum(m["recovery_eligible"] for m in per)
    total_cost = sum(m["cost_usd"] for m in per)
    return {
        "projects": len(projects),
        "finished": len(terminal),
        "task_completion_rate": round(len(completed) / len(per), 4) if per else None,
        "mean_test_pass_rate": round(sum(pass_rates) / len(pass_rates), 4) if pass_rates else None,
        "recovery_rate": round(sum(m["recovered"] for m in per) / eligible, 4) if eligible else None,
        "mean_duration_seconds": round(sum(m["duration_seconds"] for m in per) / len(per), 2) if per else None,
        "mean_retry_count": round(sum(m["retry_count"] for m in per) / len(per), 2) if per else None,
        "total_cost_usd": round(total_cost, 6),
        "cost_per_successful_task": round(total_cost / len(completed), 6) if completed else None,
        "human_intervention_rate": round(sum(1 for m in per if m["human_interventions"]) / len(per), 4) if per else None,
        "safety_violations": sum(m["safety_violations"] for m in per),
        "by_status": {s: sum(1 for p in projects if p.status == s) for s in sorted({p.status for p in projects})},
    }
