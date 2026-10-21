"""Persistence helpers used by agents and the workflow: events, agent runs, usage and budget checks."""

from __future__ import annotations

import logging
import time

from sqlalchemy import func, select

from app.db import session_scope
from app.llm.base import LLMResult
from app.models import AgentRun, UsageRecord, WorkflowEvent

log = logging.getLogger("devflow.workflow")


class BudgetExceeded(RuntimeError):
    pass


class Recorder:
    def __init__(self, project_id: int, provider: str):
        self.project_id = project_id
        self.provider = provider

    def event(self, type_: str, message: str, *, agent: str | None = None, data: dict | None = None) -> None:
        log.info("[project %s] %s: %s", self.project_id, type_, message)
        with session_scope() as db:
            db.add(WorkflowEvent(project_id=self.project_id, type=type_, agent=agent, message=message, data=data or {}))

    def start_run(self, role: str, task_id: int | None, input_summary: str) -> int:
        with session_scope() as db:
            run = AgentRun(project_id=self.project_id, task_id=task_id, role=role, input_summary=input_summary[:2000])
            db.add(run)
            db.flush()
            return run.id

    def finish_run(self, run_id: int, result: LLMResult, started: float) -> None:
        with session_scope() as db:
            run = db.get(AgentRun, run_id)
            run.status = "success"
            run.output = result.output.model_dump()
            run.model = result.model
            run.input_tokens = result.usage.input_tokens + result.usage.cache_read_tokens + result.usage.cache_write_tokens
            run.output_tokens = result.usage.output_tokens
            run.cost_usd = result.cost_usd
            run.duration_ms = int((time.monotonic() - started) * 1000)
            db.add(UsageRecord(
                project_id=self.project_id, agent_run_id=run_id, provider=self.provider, model=result.model,
                input_tokens=result.usage.input_tokens, output_tokens=result.usage.output_tokens,
                cache_read_tokens=result.usage.cache_read_tokens, cache_write_tokens=result.usage.cache_write_tokens,
                latency_ms=result.latency_ms, cost_usd=result.cost_usd,
            ))

    def fail_run(self, run_id: int, error: str, started: float) -> None:
        with session_scope() as db:
            run = db.get(AgentRun, run_id)
            run.status = "error"
            run.error = error[:4000]
            run.duration_ms = int((time.monotonic() - started) * 1000)

    def total_cost(self) -> float:
        with session_scope() as db:
            return float(db.scalar(select(func.coalesce(func.sum(UsageRecord.cost_usd), 0.0)).where(UsageRecord.project_id == self.project_id)))


class BudgetGuard:
    """Enforces the project's cost and wall-clock limits before every model call (spec 12.8)."""

    def __init__(self, recorder: Recorder, max_cost_usd: float, max_seconds: int, active_seconds_before: float):
        self.recorder = recorder
        self.max_cost_usd = max_cost_usd
        self.max_seconds = max_seconds
        self.active_before = active_seconds_before
        self.session_started = time.monotonic()

    def elapsed(self) -> float:
        return self.active_before + (time.monotonic() - self.session_started)

    def check(self) -> None:
        spent = self.recorder.total_cost()
        if spent >= self.max_cost_usd:
            raise BudgetExceeded(f"model spend ${spent:.4f} reached the limit of ${self.max_cost_usd:.2f}")
        if self.elapsed() >= self.max_seconds:
            raise BudgetExceeded(f"workflow time reached the limit of {self.max_seconds}s")
