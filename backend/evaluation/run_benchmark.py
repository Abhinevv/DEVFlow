"""Comparative evaluation: multi-agent workflow vs single-agent baseline (spec 17.6 and 18).

Each benchmark task is run under identical settings in each mode. Plans are auto-approved (recorded
as such, and not counted as human intervention). After a run, the task's hidden acceptance tests,
which no agent ever sees, are executed against the final workspace in the same sandbox.

    python -m evaluation.run_benchmark                      # all tasks, both modes, 1 repeat
    python -m evaluation.run_benchmark --tasks notes_api --repeats 3
    python -m evaluation.run_benchmark --modes multi_agent --max-cost 2

Results are written to evaluation/results/<timestamp>/ as results.json and report.md.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Keep benchmark data apart from the application's projects unless the caller chooses otherwise.
os.environ.setdefault("DEVFLOW_DATA_DIR", str(HERE / ".data"))

from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import init_db, session_scope  # noqa: E402
from app.metrics import project_metrics  # noqa: E402
from app.models import Approval, Project, ProjectStatus, User, WorkflowEvent  # noqa: E402
from app.orchestrator.runner import WorkflowRunner  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.tools.executor import create_executor  # noqa: E402
from app.tools.testing import run_pytest  # noqa: E402
from app.tools.workspace import Workspace  # noqa: E402

BENCHMARK = HERE / "benchmark"
MODES = ("multi_agent", "single_agent")


def ensure_user() -> int:
    with session_scope() as db:
        user = db.scalar(select(User).where(User.email == "benchmark@devflow.local"))
        if user is None:
            user = User(email="benchmark@devflow.local", display_name="Benchmark runner", password_hash=hash_password(os.urandom(16).hex()))
            db.add(user)
            db.flush()
        return user.id


def run_one(runner: WorkflowRunner, user_id: int, task: dict, mode: str, constraints: dict) -> int:
    with session_scope() as db:
        project = Project(owner_id=user_id, name=f"[bench] {task['name']}", requirements=task["requirements"],
                          template="fastapi_service", mode=mode, constraints=constraints)
        db.add(project)
        db.flush()
        project.workspace_path = str(get_settings().workspaces_dir / f"project-{project.id}")
        pid = project.id
    if mode == "multi_agent":
        runner.start_planning(pid).result()
        with session_scope() as db:
            project = db.get(Project, pid)
            if project.status != ProjectStatus.AWAITING_APPROVAL:
                return pid
            db.add(Approval(project_id=pid, user_id=user_id, kind="plan", decision="approved",
                            comment="auto-approved by benchmark runner", plan_version=project.plan_version))
            db.add(WorkflowEvent(project_id=pid, type="plan_approved", message="Plan auto-approved by benchmark runner"))
    runner.start_execution(pid).result()
    return pid


def acceptance(executor, project: Project, task: dict) -> dict:
    tests = BENCHMARK / task["acceptance"]
    if not project.workspace_path or not Path(project.workspace_path).exists():
        return {"passed": 0, "total": 0, "success": False, "summary": "no workspace"}
    with tempfile.TemporaryDirectory(prefix="devflow-accept-") as tmp:
        root = Path(tmp) / "ws"
        Workspace(Path(project.workspace_path)).copy_to(root)
        shutil.copytree(tests, root / "acceptance_tests", ignore=shutil.ignore_patterns("__pycache__"))
        report = run_pytest(executor, Workspace(root), selectors=["acceptance_tests"])
    return {"passed": report.passed, "total": report.total, "success": report.success and report.total > 0,
            "summary": report.summary, "failures": [f.nodeid for f in report.failures]}


def summarise(rows: list[dict]) -> dict:
    if not rows:
        return {}
    ok = [r for r in rows if r["acceptance"]["success"]]
    eligible = sum(r["metrics"]["recovery_eligible"] for r in rows)
    costs = [r["metrics"]["cost_usd"] for r in rows]
    pass_rates = [r["metrics"]["test_pass_rate"] for r in rows if r["metrics"]["test_pass_rate"] is not None]
    acc_rates = [r["acceptance"]["passed"] / r["acceptance"]["total"] for r in rows if r["acceptance"]["total"]]
    return {
        "runs": len(rows),
        "task_completion_rate": round(len(ok) / len(rows), 4),
        "acceptance_pass_rate": round(statistics.mean(acc_rates), 4) if acc_rates else None,
        "internal_test_pass_rate": round(statistics.mean(pass_rates), 4) if pass_rates else None,
        "recovery_rate": round(sum(r["metrics"]["recovered"] for r in rows) / eligible, 4) if eligible else None,
        "mean_retries": round(statistics.mean(r["metrics"]["retry_count"] for r in rows), 2),
        "mean_duration_seconds": round(statistics.mean(r["metrics"]["duration_seconds"] for r in rows), 2),
        "mean_cost_usd": round(statistics.mean(costs), 6),
        "cost_per_successful_task": round(sum(costs) / len(ok), 6) if ok else None,
        "human_intervention_rate": round(sum(1 for r in rows if r["metrics"]["human_interventions"]) / len(rows), 4),
        "safety_violations": sum(r["metrics"]["safety_violations"] for r in rows),
        "statuses": {s: sum(1 for r in rows if r["status"] == s) for s in sorted({r["status"] for r in rows})},
    }


def render(summary: dict, rows: list[dict], config: dict) -> str:
    def fmt(v, pct=False):
        if v is None:
            return "—"
        return f"{v * 100:.0f}%" if pct else str(v)

    lines = ["# DevFlow AI benchmark report", "", f"Generated {config['generated_at']}", "",
             f"Provider `{config['llm_provider']}`, model `{config['llm_model']}`, executor `{config['executor']}`, "
             f"limits {config['constraints']}, repeats {config['repeats']}.", "",
             "| Metric | " + " | ".join(summary) + " |", "|---|" + "---|" * len(summary)]
    metrics = [("Runs", "runs", False), ("Task completion (all acceptance tests pass)", "task_completion_rate", True),
               ("Acceptance test pass rate", "acceptance_pass_rate", True), ("Internal test pass rate", "internal_test_pass_rate", True),
               ("Recovery rate", "recovery_rate", True), ("Mean retries", "mean_retries", False),
               ("Mean duration (s)", "mean_duration_seconds", False), ("Mean cost (USD)", "mean_cost_usd", False),
               ("Cost per successful task (USD)", "cost_per_successful_task", False),
               ("Human intervention rate", "human_intervention_rate", True), ("Safety violations", "safety_violations", False)]
    for title, key, pct in metrics:
        lines.append(f"| {title} | " + " | ".join(fmt(s.get(key), pct) for s in summary.values()) + " |")
    lines += ["", "## Runs", "", "| Task | Mode | Status | Acceptance | Internal tests | Retries | Duration (s) | Cost |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        a, m = r["acceptance"], r["metrics"]
        lines.append(f"| {r['task']} | {r['mode']} | {r['status']} | {a['passed']}/{a['total']} | {fmt(m['test_pass_rate'], True)} | "
                     f"{m['retry_count']} | {m['duration_seconds']} | ${m['cost_usd']:.4f} |")
    if config["llm_provider"] == "mock":
        lines += ["", "> The mock provider scripts a single Notes API regardless of requirements, so only `notes_api` is meaningful "
                  "with it. Use `DEVFLOW_LLM_PROVIDER=anthropic` for real measurements."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    tasks_all = json.loads((BENCHMARK / "tasks.json").read_text(encoding="utf-8"))
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tasks", help="comma-separated task ids (default: all; notes_api only with the mock provider)")
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-debug", type=int, default=settings.max_debug_attempts)
    parser.add_argument("--max-review-rounds", type=int, default=settings.max_review_rounds)
    parser.add_argument("--max-cost", type=float, default=settings.max_cost_usd)
    args = parser.parse_args(argv)

    default_tasks = "notes_api" if settings.llm_provider == "mock" else ",".join(t["id"] for t in tasks_all)
    wanted = (args.tasks or default_tasks).split(",")
    tasks = [t for t in tasks_all if t["id"] in wanted]
    modes = [m for m in args.modes.split(",") if m in MODES]
    if not tasks or not modes:
        parser.error("no matching tasks or modes")

    init_db()
    executor = create_executor(settings)
    runner = WorkflowRunner(settings, max_workers=1)
    user_id = ensure_user()
    constraints = {"max_debug_attempts": args.max_debug, "max_review_rounds": args.max_review_rounds, "max_cost_usd": args.max_cost}

    rows = []
    for task in tasks:
        for mode in modes:
            for rep in range(args.repeats):
                print(f"-> {task['id']} / {mode} / run {rep + 1}", flush=True)
                pid = run_one(runner, user_id, task, mode, constraints)
                with session_scope() as db:
                    project = db.get(Project, pid)
                    metrics = project_metrics(db, project)
                    status, reason = project.status, project.status_reason
                acc = acceptance(executor, project, task)
                rows.append({"task": task["id"], "mode": mode, "repeat": rep + 1, "project_id": pid, "status": status,
                             "reason": reason, "acceptance": acc, "metrics": metrics})
                print(f"   {status}: acceptance {acc['passed']}/{acc['total']} — {reason}", flush=True)
    runner.shutdown()

    summary = {m: summarise([r for r in rows if r["mode"] == m]) for m in modes}
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    config = {"generated_at": stamp, "llm_provider": settings.llm_provider,
              "llm_model": settings.llm_model if settings.llm_provider == "anthropic" else "mock-scripted",
              "executor": executor.name, "constraints": constraints, "repeats": args.repeats}
    out = HERE / "results" / stamp
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"config": config, "summary": summary, "runs": rows}, indent=2, default=str), encoding="utf-8")
    report = render(summary, rows, config)
    (out / "report.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    print(f"Saved to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
