"""Background execution of workflows and project status transitions (FR-10, FR-17, spec 12.1)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import session_scope
from app.llm import LLMError, create_llm_client
from app.models import Project, ProjectStatus, WorkflowEvent
from app.orchestrator.context import CancelRequested, Halt, PauseRequested, RunContext
from app.orchestrator.graph import ExecutionWorkflow, PlanningWorkflow
from app.orchestrator.recorder import BudgetExceeded
from app.tools.executor import ExecutionUnavailable, create_executor

log = logging.getLogger("devflow.runner")


class AlreadyRunning(RuntimeError):
    pass


class WorkflowRunner:
    def __init__(self, settings: Settings | None = None, max_workers: int = 2):
        self.settings = settings or get_settings()
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="devflow-wf")
        self._active: set[int] = set()
        self._lock = threading.Lock()

    def is_active(self, project_id: int) -> bool:
        with self._lock:
            return project_id in self._active

    def start_planning(self, project_id: int, feedback: str | None = None) -> Future:
        return self._submit(project_id, ProjectStatus.PLANNING, lambda: self._plan(project_id, feedback))

    def start_execution(self, project_id: int) -> Future:
        return self._submit(project_id, ProjectStatus.QUEUED, lambda: self._execute(project_id))

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # --- internals ---------------------------------------------------------------------

    def _submit(self, project_id: int, initial_status: str, fn: Callable[[], None]) -> Future:
        with self._lock:
            if project_id in self._active:
                raise AlreadyRunning(f"project {project_id} already has a running workflow")
            self._active.add(project_id)
        with session_scope() as db:
            project = db.get(Project, project_id)
            project.status = initial_status
            project.status_reason = None
            project.control = None

        def job():
            try:
                fn()
            finally:
                with self._lock:
                    self._active.discard(project_id)

        return self._pool.submit(job)

    def _plan(self, project_id: int, feedback: str | None) -> None:
        try:
            ctx = RunContext(project_id, self.settings, create_llm_client(self.settings), executor=None)
            PlanningWorkflow(ctx, feedback).run()
        except LLMError as exc:
            self._finish(project_id, ProjectStatus.DRAFT, f"Planning failed: {exc}", "planning_failed")
        except Exception as exc:  # noqa: BLE001 - surface every failure to the user
            log.exception("planning crashed for project %s", project_id)
            self._finish(project_id, ProjectStatus.DRAFT, f"Planning failed unexpectedly: {exc}", "planning_failed")

    def _execute(self, project_id: int) -> None:
        started = time.monotonic()
        with session_scope() as db:
            project = db.get(Project, project_id)
            project.status = ProjectStatus.RUNNING
            project.started_at = project.started_at or datetime.now(UTC)
            project.finished_at = None
            project.config_snapshot = self._config_snapshot(project)
        try:
            executor = create_executor(self.settings)
            ctx = RunContext(project_id, self.settings, create_llm_client(self.settings), executor=executor)
            if not executor.isolated:
                ctx.recorder.event("security_notice", "Using the local executor: generated code runs WITHOUT container isolation")
            ExecutionWorkflow(ctx).run()
            self._accumulate(project_id, started, finished=True)
        except PauseRequested:
            self._finish(project_id, ProjectStatus.PAUSED, "Paused by user", "workflow_paused", started)
        except CancelRequested:
            self._finish(project_id, ProjectStatus.CANCELLED, "Cancelled by user", "workflow_cancelled", started, finished=True)
        except Halt as exc:
            self._finish(project_id, exc.status, exc.reason, "workflow_halted", started, finished=exc.status in ProjectStatus.TERMINAL)
        except BudgetExceeded as exc:
            self._finish(project_id, ProjectStatus.FAILED, f"Limit reached: {exc}", "limit_reached", started, finished=True)
        except ExecutionUnavailable as exc:
            self._finish(project_id, ProjectStatus.BLOCKED, str(exc), "executor_unavailable", started)
        except LLMError as exc:
            self._finish(project_id, ProjectStatus.BLOCKED, f"Model call failed: {exc}. Resume to retry.", "model_error", started)
        except Exception as exc:  # noqa: BLE001
            log.exception("workflow crashed for project %s", project_id)
            self._finish(project_id, ProjectStatus.BLOCKED, f"Unexpected error: {exc}. Resume to retry.", "workflow_error", started)

    def _config_snapshot(self, project: Project) -> dict:
        from app.orchestrator.context import Limits
        from app.templates import load_template

        s = self.settings
        template = load_template(project.template)
        return {
            "llm_provider": s.llm_provider,
            "llm_model": s.llm_model if s.llm_provider == "anthropic" else "mock-scripted",
            "llm_effort": s.llm_effort,
            "template": template.id,
            "template_version": template.version,
            "approved_dependencies": template.approved_dependencies,
            "executor": s.executor,
            "sandbox_image": s.sandbox_image,
            "limits": vars(Limits.for_project(s, project.constraints or {})),
            "exec_timeout_seconds": s.exec_timeout_seconds,
            "exec_memory_mb": s.exec_memory_mb,
        }

    def _accumulate(self, project_id: int, started: float, finished: bool) -> None:
        with session_scope() as db:
            project = db.get(Project, project_id)
            project.active_seconds = (project.active_seconds or 0.0) + (time.monotonic() - started)
            if finished:
                project.finished_at = datetime.now(UTC)

    def _finish(self, project_id: int, status: str, reason: str, event_type: str, started: float | None = None, finished: bool = False) -> None:
        with session_scope() as db:
            project = db.get(Project, project_id)
            project.status = status
            project.status_reason = reason
            project.control = None
            if started is not None:
                project.active_seconds = (project.active_seconds or 0.0) + (time.monotonic() - started)
            if finished:
                project.finished_at = datetime.now(UTC)
            db.add(WorkflowEvent(project_id=project_id, type=event_type, message=reason))


def recover_interrupted() -> int:
    """Projects left mid-run by a server stop become resumable instead of appearing to run forever."""
    count = 0
    with session_scope() as db:
        for project in db.scalars(select(Project).where(Project.status.in_(list(ProjectStatus.ACTIVE)))):
            planning = project.status == ProjectStatus.PLANNING
            project.status = ProjectStatus.DRAFT if planning else ProjectStatus.BLOCKED
            project.status_reason = "Interrupted by a server restart; " + ("generate the plan again" if planning else "resume to continue")
            db.add(WorkflowEvent(project_id=project.id, type="workflow_interrupted", message=project.status_reason))
            count += 1
    return count
