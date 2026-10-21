"""Structured outputs exchanged with the agents. Every model response is validated against one of these."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

TASK_ID_RE = re.compile(r"^T\d{1,3}$")


class Component(BaseModel):
    name: str
    responsibility: str
    interfaces: list[str] = Field(description="Public functions, endpoints or classes this component exposes.")


class PlanTask(BaseModel):
    id: str = Field(description="Task identifier of the form T1, T2, ...")
    title: str
    description: str = Field(description="What to build, precise enough for a developer to implement without guessing.")
    depends_on: list[str] = Field(description="Ids of tasks that must be completed first.")
    expected_outputs: list[str] = Field(description="Workspace-relative file paths this task creates or modifies.")
    acceptance_criteria: list[str] = Field(description="Observable, testable conditions for completion.")

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not TASK_ID_RE.match(value):
            raise ValueError(f"task id {value!r} must look like T1, T2, ...")
        return value


class Plan(BaseModel):
    summary: str
    functional_requirements: list[str]
    non_functional_requirements: list[str]
    assumptions: list[str]
    constraints: list[str]
    clarifications_needed: list[str] = Field(description="Questions whose answers would materially change the plan. Empty if none.")
    acceptance_criteria: list[str] = Field(description="Project-level acceptance criteria.")
    components: list[Component]
    tech_stack: list[str]
    directory_structure: list[str] = Field(description="Expected workspace-relative file paths of the finished project.")
    tasks: list[PlanTask]

    @model_validator(mode="after")
    def _check_graph(self) -> Plan:
        errors = plan_graph_errors(self)
        if errors:
            raise ValueError("; ".join(errors))
        return self


def plan_graph_errors(plan: Plan) -> list[str]:
    """Return dependency-graph problems: duplicates, unknown dependencies, cycles."""
    errors: list[str] = []
    ids = [t.id for t in plan.tasks]
    if not ids:
        errors.append("plan has no tasks")
    if len(set(ids)) != len(ids):
        errors.append("duplicate task ids")
    known = set(ids)
    for task in plan.tasks:
        for dep in task.depends_on:
            if dep not in known:
                errors.append(f"{task.id} depends on unknown task {dep}")
            if dep == task.id:
                errors.append(f"{task.id} depends on itself")
    if not errors and topological_order(plan.tasks) is None:
        errors.append("task dependencies contain a cycle")
    return errors


def topological_order(tasks: list[PlanTask]) -> list[str] | None:
    remaining = {t.id: set(t.depends_on) for t in tasks}
    order: list[str] = []
    while remaining:
        ready = sorted(k for k, deps in remaining.items() if not deps)
        if not ready:
            return None
        for key in ready:
            order.append(key)
            del remaining[key]
        for deps in remaining.values():
            deps.difference_update(ready)
    return order


class FileChange(BaseModel):
    path: str = Field(description="Workspace-relative POSIX path, e.g. app/main.py")
    action: Literal["create", "modify", "delete"]
    content: str = Field(description="Complete new file content. Empty string for delete.")


class DeveloperOutput(BaseModel):
    summary: str
    files: list[FileChange]
    notes: list[str] = Field(description="Assumptions or follow-ups worth recording. Empty if none.")


class DebuggerOutput(BaseModel):
    root_cause: str
    explanation: str
    confidence: Literal["low", "medium", "high"]
    files: list[FileChange] = Field(description="Minimal set of complete-file replacements that fix the failure.")
    validation_steps: list[str]


class RequirementCoverage(BaseModel):
    requirement: str
    status: Literal["met", "partial", "missing"]
    evidence: str


class Finding(BaseModel):
    severity: Literal["critical", "high", "medium", "low", "info"]
    category: Literal["correctness", "requirements", "security", "maintainability", "testing", "performance", "style"]
    file: str = Field(description="Workspace-relative path, or empty if project-wide.")
    line: int = Field(description="1-based line number, or 0 if not applicable.")
    description: str
    recommendation: str
    actionable: bool = Field(description="True if a developer can fix it now without a human decision.")


class ReviewReport(BaseModel):
    summary: str
    requirements_coverage: list[RequirementCoverage]
    findings: list[Finding]
    recommendation: Literal["accept", "revise", "human_review"]
