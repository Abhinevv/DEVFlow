"""Tester agent. Deliberately deterministic: its verdicts come from real execution, never from a model."""

from __future__ import annotations

from app.db import session_scope
from app.models import TestRun
from app.orchestrator.recorder import Recorder
from app.tools.executor import Executor
from app.tools.testing import TestReport, run_pytest
from app.tools.workspace import Workspace


class TesterAgent:
    role = "tester"

    def __init__(self, executor: Executor, recorder: Recorder):
        self.executor = executor
        self.recorder = recorder

    def run(self, workspace: Workspace, *, kind: str, task_id: int | None = None, selectors: list[str] | None = None,
            checks: dict | None = None) -> tuple[TestReport, int]:
        label = f"{len(selectors)} previously failing test(s)" if selectors else "full suite"
        self.recorder.event("tests_started", f"Running {label} ({kind})", agent=self.role, data={"kind": kind, "task_id": task_id})
        report = run_pytest(self.executor, workspace, selectors)
        with session_scope() as db:
            row = TestRun(
                project_id=self.recorder.project_id, task_id=task_id, kind=kind, command=report.command,
                exit_code=report.exit_code, passed=report.passed, failed=report.failed, errors=report.errors,
                skipped=report.skipped, total=report.total, success=report.success, duration_ms=report.duration_ms,
                summary=report.summary, failures=[f.as_dict() for f in report.failures], checks=checks or {},
                log=report.log, executor=report.executor or self.executor.name, isolated=report.isolated,
            )
            db.add(row)
            db.flush()
            run_id = row.id
        self.recorder.event(
            "tests_passed" if report.success else "tests_failed",
            report.summary,
            agent=self.role,
            data={"test_run_id": run_id, "kind": kind, "passed": report.passed, "failed": report.failed, "errors": report.errors},
        )
        return report, run_id


def report_for_model(report: TestReport) -> dict:
    """Compact, model-facing view of a test report."""
    return {
        "summary": report.summary,
        "exit_code": report.exit_code,
        "failures": [f.as_dict() for f in report.failures[:15]],
        "log_tail": report.log[-8000:],
    }
