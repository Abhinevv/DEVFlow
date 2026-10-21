"""Everything one workflow run needs: project data, workspace, tools, agents, limits."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from app.agents import DebuggerAgent, DeveloperAgent, PlannerAgent, ReviewerAgent, SingleAgent, TesterAgent
from app.agents.schemas import FileChange
from app.config import Settings
from app.db import session_scope
from app.llm import LLMClient
from app.models import Artifact, Project, WorkflowEvent
from app.orchestrator.recorder import BudgetGuard, Recorder
from app.templates import Template, load_template, unapproved_requirements
from app.tools.executor import Executor
from app.tools.git_tools import GitRepo
from app.tools.workspace import PathViolation, Workspace

HARD_CAPS = {"max_debug_attempts": 10, "max_review_rounds": 5}


class Halt(Exception):
    """Stop the workflow with a specific project status."""

    def __init__(self, status: str, reason: str):
        super().__init__(reason)
        self.status = status
        self.reason = reason


class PauseRequested(Exception):
    pass


class CancelRequested(Exception):
    pass


@dataclass
class Limits:
    max_debug_attempts: int
    max_review_rounds: int
    max_cost_usd: float
    max_workflow_seconds: int
    max_safety_violations: int

    @classmethod
    def for_project(cls, settings: Settings, constraints: dict) -> Limits:
        def pick(name: str, default, cap):
            value = constraints.get(name, default)
            try:
                value = type(default)(value)
            except (TypeError, ValueError):
                value = default
            return max(0, min(value, cap))

        return cls(
            max_debug_attempts=pick("max_debug_attempts", settings.max_debug_attempts, HARD_CAPS["max_debug_attempts"]),
            max_review_rounds=pick("max_review_rounds", settings.max_review_rounds, HARD_CAPS["max_review_rounds"]),
            max_cost_usd=pick("max_cost_usd", settings.max_cost_usd, settings.max_cost_usd),
            max_workflow_seconds=pick("max_workflow_seconds", settings.max_workflow_seconds, settings.max_workflow_seconds),
            max_safety_violations=settings.max_safety_violations,
        )


class RunContext:
    def __init__(self, project_id: int, settings: Settings, llm: LLMClient, executor: Executor | None):
        self.project_id = project_id
        self.settings = settings
        self.llm = llm
        with session_scope() as db:
            project = db.get(Project, project_id)
            self.name = project.name
            self.requirements = project.requirements
            self.mode = project.mode
            self.constraints = dict(project.constraints or {})
            self.plan = project.plan
            self.template_id = project.template
            active_before = project.active_seconds or 0.0
            workspace_path = project.workspace_path or str(settings.workspaces_dir / f"project-{project_id}")
            project.workspace_path = workspace_path
        self.template: Template = load_template(self.template_id)
        self.limits = Limits.for_project(settings, self.constraints)
        self.workspace = Workspace(Path(workspace_path), max_file_bytes=settings.max_file_bytes, max_files=settings.max_workspace_files)
        self.git = GitRepo(self.workspace.root)
        self.recorder = Recorder(project_id, llm.provider)
        self.budget = BudgetGuard(self.recorder, self.limits.max_cost_usd, self.limits.max_workflow_seconds, active_before)
        self.executor = executor
        self.meta = {"mock_scenario": self.constraints.get("mock_scenario", "default")}

        self.planner = PlannerAgent(llm, self.recorder, self.budget)
        self.developer = DeveloperAgent(llm, self.recorder, self.budget)
        self.debugger = DebuggerAgent(llm, self.recorder, self.budget)
        self.reviewer = ReviewerAgent(llm, self.recorder, self.budget)
        self.single = SingleAgent(llm, self.recorder, self.budget)
        self.tester = TesterAgent(executor, self.recorder) if executor else None

    def check_control(self) -> None:
        with session_scope() as db:
            control = db.get(Project, self.project_id).control
        if control == "cancel":
            raise CancelRequested()
        if control == "pause":
            raise PauseRequested()

    def safety_violations(self) -> int:
        with session_scope() as db:
            return db.scalar(select(func.count()).select_from(WorkflowEvent).where(
                WorkflowEvent.project_id == self.project_id, WorkflowEvent.type == "safety_violation"))

    def apply_changes(self, changes: list[FileChange], *, task_id: int | None, message: str) -> tuple[list[dict], list[str]]:
        """Apply agent file changes through the workspace policy, commit them, and record artifacts.

        Returns (applied, rejection_messages). Rejections are fed back to the agent on retry."""
        applied: list[dict] = []
        rejected: list[str] = []
        violations: list[tuple[str, str]] = []

        if len(changes) > self.settings.max_files_per_change:
            violations.append(("*", f"change set of {len(changes)} files exceeds the limit of {self.settings.max_files_per_change}"))
            changes = []

        for change in changes:
            try:
                rel = self.workspace.validate(change.path)
                if rel == "requirements.txt" and change.action != "delete":
                    bad = unapproved_requirements(change.content, self.template.approved_dependencies)
                    if bad:
                        raise PathViolation(f"requirements.txt adds unapproved dependencies: {', '.join(bad)}")
                result = self.workspace.delete(rel) if change.action == "delete" else self.workspace.write(rel, change.content)
                applied.append({"path": result.path, "action": result.action, "sha256": result.sha256, "size": result.size})
            except PathViolation as exc:
                violations.append((change.path, str(exc)))

        for path, reason in violations:
            rejected.append(f"Rejected change to {path!r}: {reason}")
            self.recorder.event("safety_violation", f"Rejected file operation on {path!r}: {reason}", data={"path": path, "reason": reason})

        commit = self.git.commit_all(message) if applied else self.git.head()
        with session_scope() as db:
            for item in applied:
                db.add(Artifact(project_id=self.project_id, task_id=task_id, path=item["path"], action=item["action"],
                                sha256=item["sha256"], size=item["size"], commit=commit))
            for path, reason in violations:
                db.add(Artifact(project_id=self.project_id, task_id=task_id, path=path[:500], action="rejected", note=reason))
        if applied:
            self.recorder.event("files_changed", f"{len(applied)} file(s) changed: {message}",
                                data={"files": [a["path"] for a in applied], "commit": commit, "task_id": task_id})

        if violations and self.safety_violations() >= self.limits.max_safety_violations:
            raise Halt("failed", f"Safety stop: {self.safety_violations()} rejected file operations reached the limit")
        return applied, rejected
