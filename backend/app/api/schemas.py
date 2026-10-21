"""Request and response models for the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Format check only: self-hosted installs often use internal or reserved domains (.local, .test).
EMAIL_PATTERN = r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$"
Email = Annotated[str, Field(pattern=EMAIL_PATTERN, max_length=254)]

ALLOWED_CONSTRAINTS = {"max_debug_attempts", "max_review_rounds", "max_cost_usd", "max_workflow_seconds", "mock_scenario", "notes"}
MOCK_SCENARIOS = {"default", "clean", "unfixable", "unsafe"}


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterIn(BaseModel):
    email: Email
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: Email
    password: str = Field(max_length=200)


class UserOut(ORM):
    id: int
    email: str
    display_name: str
    role: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    requirements: str = Field(min_length=20, max_length=20_000)
    template: str = "fastapi_service"
    mode: Literal["multi_agent", "single_agent"] = "multi_agent"
    constraints: dict[str, Any] = Field(default_factory=dict)

    @field_validator("constraints")
    @classmethod
    def _check_constraints(cls, value: dict[str, Any]) -> dict[str, Any]:
        unknown = set(value) - ALLOWED_CONSTRAINTS
        if unknown:
            raise ValueError(f"unknown constraints: {', '.join(sorted(unknown))}")
        for key in ("max_debug_attempts", "max_review_rounds", "max_workflow_seconds"):
            if key in value and (not isinstance(value[key], int) or value[key] < 0):
                raise ValueError(f"{key} must be a non-negative integer")
        if "max_cost_usd" in value and (not isinstance(value["max_cost_usd"], (int, float)) or value["max_cost_usd"] <= 0):
            raise ValueError("max_cost_usd must be positive")
        if "mock_scenario" in value and value["mock_scenario"] not in MOCK_SCENARIOS:
            raise ValueError(f"mock_scenario must be one of {sorted(MOCK_SCENARIOS)}")
        if "notes" in value and (not isinstance(value["notes"], str) or len(value["notes"]) > 4000):
            raise ValueError("notes must be a string of at most 4000 characters")
        return value


class ProjectOut(ORM):
    id: int
    name: str
    template: str
    mode: str
    status: str
    status_reason: str | None
    control: str | None
    plan_version: int
    review_round: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    active_seconds: float


class ProjectDetail(ProjectOut):
    requirements: str
    constraints: dict
    plan: dict | None
    config_snapshot: dict
    has_package: bool = False
    running: bool = False
    metrics: dict = Field(default_factory=dict)


class TaskOut(ORM):
    id: int
    key: str
    title: str
    description: str
    depends_on: list[str]
    expected_outputs: list[str]
    acceptance_criteria: list[str]
    status: str
    attempts: int
    debug_attempts: int
    evidence: dict
    started_at: datetime | None
    finished_at: datetime | None


class AgentRunOut(ORM):
    id: int
    task_id: int | None
    role: str
    status: str
    input_summary: str
    output: dict | None
    error: str | None
    model: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    started_at: datetime
    duration_ms: int


class TestRunOut(ORM):
    id: int
    task_id: int | None
    kind: str
    command: str
    exit_code: int
    passed: int
    failed: int
    errors: int
    skipped: int
    total: int
    success: bool
    duration_ms: int
    summary: str
    failures: list
    checks: dict
    log: str
    executor: str
    isolated: bool
    created_at: datetime


class FindingOut(ORM):
    id: int
    round: int
    source: str
    severity: str
    category: str
    file: str
    line: int
    description: str
    recommendation: str
    actionable: bool
    status: str


class EventOut(ORM):
    id: int
    type: str
    agent: str | None
    message: str
    data: dict
    created_at: datetime


class ApprovalOut(ORM):
    id: int
    kind: str
    decision: str
    comment: str | None
    plan_version: int | None
    created_at: datetime


class ArtifactOut(ORM):
    id: int
    task_id: int | None
    kind: str
    path: str
    action: str
    sha256: str | None
    size: int
    commit: str | None
    note: str | None
    created_at: datetime


class DecisionIn(BaseModel):
    comment: str | None = Field(default=None, max_length=4000)
    # Approve only if the plan is still this version (guards approval links against revised plans).
    plan_version: int | None = None


class ReviseIn(BaseModel):
    feedback: str = Field(min_length=3, max_length=4000)
