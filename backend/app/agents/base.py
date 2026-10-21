"""Common agent behaviour: one structured model call, fully recorded."""

from __future__ import annotations

import time
from typing import Any, ClassVar

from pydantic import BaseModel

from app.llm.base import LLMClient, LLMError
from app.orchestrator.recorder import BudgetGuard, Recorder


class Agent:
    role: ClassVar[str]
    output_model: ClassVar[type[BaseModel]]
    system_prompt: ClassVar[str]

    def __init__(self, llm: LLMClient, recorder: Recorder, budget: BudgetGuard):
        self.llm = llm
        self.recorder = recorder
        self.budget = budget

    def call(self, prompt: str, *, summary: str, task_id: int | None = None, meta: dict[str, Any] | None = None) -> BaseModel:
        self.budget.check()
        started = time.monotonic()
        run_id = self.recorder.start_run(self.role, task_id, summary)
        self.recorder.event("agent_started", summary, agent=self.role, data={"agent_run_id": run_id, "task_id": task_id})
        try:
            result = self.llm.generate(role=self.role, system=self.system_prompt, prompt=prompt, output_model=self.output_model, meta=meta)
        except LLMError as exc:
            self.recorder.fail_run(run_id, str(exc), started)
            self.recorder.event("agent_failed", str(exc), agent=self.role, data={"agent_run_id": run_id})
            raise
        self.recorder.finish_run(run_id, result, started)
        self.recorder.event(
            "agent_finished",
            f"{self.role} finished in {result.latency_ms / 1000:.1f}s",
            agent=self.role,
            data={"agent_run_id": run_id, "tokens_in": result.usage.input_tokens, "tokens_out": result.usage.output_tokens, "cost_usd": result.cost_usd},
        )
        return result.output
