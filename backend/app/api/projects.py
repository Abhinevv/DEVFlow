"""Project, workflow-control and inspection endpoints."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AgentRunOut,
    ApprovalOut,
    ArtifactOut,
    DecisionIn,
    EventOut,
    FindingOut,
    ProjectCreate,
    ProjectDetail,
    ProjectOut,
    ReviseIn,
    TaskOut,
    TestRunOut,
)
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.metrics import project_metrics
from app.models import (
    AgentRun,
    Approval,
    Artifact,
    Project,
    ProjectStatus,
    ReviewFinding,
    Task,
    TestRun,
    User,
    WorkflowEvent,
)
from app.orchestrator.delivery import build_report, render_markdown
from app.orchestrator.runner import AlreadyRunning, WorkflowRunner
from app.security import current_user, owned_project
from app.templates import load_template
from app.tools.git_tools import GitError, GitRepo
from app.tools.workspace import PathViolation, Workspace

router = APIRouter(prefix="/api/projects", tags=["projects"])


def get_runner(request: Request) -> WorkflowRunner:
    return request.app.state.runner


def _conflict(message: str) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, message)


def _detail(db: Session, project: Project, runner: WorkflowRunner) -> ProjectDetail:
    out = ProjectDetail.model_validate(project)
    out.has_package = bool(project.package_path and Path(project.package_path).is_file())
    out.running = runner.is_active(project.id)
    out.metrics = project_metrics(db, project)
    return out


def _record(db: Session, project: Project, user: User, kind: str, decision: str, comment: str | None = None) -> None:
    db.add(Approval(project_id=project.id, user_id=user.id, kind=kind, decision=decision, comment=comment, plan_version=project.plan_version))
    db.add(WorkflowEvent(project_id=project.id, type=f"{kind}_{decision}", message=f"{user.display_name}: {kind} {decision.replace('_', ' ')}"
                         + (f" — {comment}" if comment else ""), data={"user_id": user.id}))


def _start(runner: WorkflowRunner, fn, project_id: int) -> None:
    try:
        fn(project_id)
    except AlreadyRunning as exc:
        raise _conflict(str(exc)) from exc


# --- CRUD -----------------------------------------------------------------------------------

@router.get("", response_model=list[ProjectOut])
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return db.scalars(select(Project).where(Project.owner_id == user.id).order_by(Project.id.desc())).all()


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, user: User = Depends(current_user), db: Session = Depends(get_db),
                   runner: WorkflowRunner = Depends(get_runner)):
    try:
        load_template(body.template)
    except KeyError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown template {body.template!r}") from None
    project = Project(owner_id=user.id, name=body.name.strip(), requirements=body.requirements.strip(), template=body.template,
                      mode=body.mode, constraints=body.constraints)
    db.add(project)
    db.flush()
    project.workspace_path = str(get_settings().workspaces_dir / f"project-{project.id}")
    db.add(WorkflowEvent(project_id=project.id, type="project_created", message=f"Project created by {user.display_name} ({body.mode})"))
    db.commit()
    return _detail(db, project, runner)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(project: Project = Depends(owned_project), db: Session = Depends(get_db), runner: WorkflowRunner = Depends(get_runner)):
    return _detail(db, project, runner)


# --- planning & approval (FR-02..FR-04) ------------------------------------------------------

@router.post("/{project_id}/plan", response_model=ProjectDetail, status_code=status.HTTP_202_ACCEPTED)
def generate_plan(project: Project = Depends(owned_project), db: Session = Depends(get_db), runner: WorkflowRunner = Depends(get_runner)):
    if project.mode != "multi_agent":
        raise _conflict("Single-agent baseline projects are not planned; start them directly")
    if project.status not in (ProjectStatus.DRAFT, ProjectStatus.PLAN_REJECTED, ProjectStatus.AWAITING_APPROVAL):
        raise _conflict(f"Cannot plan a project in status '{project.status}'")
    _start(runner, runner.start_planning, project.id)
    db.refresh(project)
    return _detail(db, project, runner)


@router.post("/{project_id}/plan/approve", response_model=ProjectDetail, status_code=status.HTTP_202_ACCEPTED)
def approve_plan(body: DecisionIn, project: Project = Depends(owned_project), user: User = Depends(current_user),
                 db: Session = Depends(get_db), runner: WorkflowRunner = Depends(get_runner)):
    if project.status != ProjectStatus.AWAITING_APPROVAL:
        raise _conflict("There is no plan awaiting approval")
    if body.plan_version is not None and body.plan_version != project.plan_version:
        raise _conflict(f"Plan v{body.plan_version} was superseded by v{project.plan_version}; review the current plan")
    _record(db, project, user, "plan", "approved", body.comment)
    db.commit()
    _start(runner, runner.start_execution, project.id)
    db.refresh(project)
    return _detail(db, project, runner)


@router.post("/{project_id}/plan/reject", response_model=ProjectDetail)
def reject_plan(body: DecisionIn, project: Project = Depends(owned_project), user: User = Depends(current_user),
                db: Session = Depends(get_db), runner: WorkflowRunner = Depends(get_runner)):
    if project.status != ProjectStatus.AWAITING_APPROVAL:
        raise _conflict("There is no plan awaiting approval")
    _record(db, project, user, "plan", "rejected", body.comment)
    project.status = ProjectStatus.PLAN_REJECTED
    project.status_reason = body.comment or "Plan rejected"
    db.commit()
    return _detail(db, project, runner)


@router.post("/{project_id}/plan/revise", response_model=ProjectDetail, status_code=status.HTTP_202_ACCEPTED)
def revise_plan(body: ReviseIn, project: Project = Depends(owned_project), user: User = Depends(current_user),
                db: Session = Depends(get_db), runner: WorkflowRunner = Depends(get_runner)):
    if project.status not in (ProjectStatus.AWAITING_APPROVAL, ProjectStatus.PLAN_REJECTED):
        raise _conflict("The plan can only be revised before execution starts")
    _record(db, project, user, "plan", "revision_requested", body.feedback)
    db.commit()
    _start(runner, lambda pid: runner.start_planning(pid, body.feedback), project.id)
    db.refresh(project)
    return _detail(db, project, runner)


# --- workflow control (FR-17) ----------------------------------------------------------------

@router.post("/{project_id}/start", response_model=ProjectDetail, status_code=status.HTTP_202_ACCEPTED)
def start_baseline(project: Project = Depends(owned_project), user: User = Depends(current_user),
                   db: Session = Depends(get_db), runner: WorkflowRunner = Depends(get_runner)):
    if project.mode != "single_agent" or project.status != ProjectStatus.DRAFT:
        raise _conflict("Only draft single-agent baseline projects start without a plan")
    _start(runner, runner.start_execution, project.id)
    db.refresh(project)
    return _detail(db, project, runner)


@router.post("/{project_id}/pause", response_model=ProjectDetail)
def pause(project: Project = Depends(owned_project), user: User = Depends(current_user), db: Session = Depends(get_db),
          runner: WorkflowRunner = Depends(get_runner)):
    if project.status not in (ProjectStatus.QUEUED, ProjectStatus.RUNNING):
        raise _conflict("Only a running workflow can be paused")
    project.control = "pause"
    _record(db, project, user, "pause", "requested")
    db.commit()
    return _detail(db, project, runner)


@router.post("/{project_id}/resume", response_model=ProjectDetail, status_code=status.HTTP_202_ACCEPTED)
def resume(body: DecisionIn, project: Project = Depends(owned_project), user: User = Depends(current_user),
           db: Session = Depends(get_db), runner: WorkflowRunner = Depends(get_runner)):
    if project.status not in ProjectStatus.RESUMABLE:
        raise _conflict(f"A project in status '{project.status}' cannot be resumed")
    _record(db, project, user, "resume", "approved", body.comment)
    db.commit()
    _start(runner, runner.start_execution, project.id)
    db.refresh(project)
    return _detail(db, project, runner)


@router.post("/{project_id}/cancel", response_model=ProjectDetail)
def cancel(body: DecisionIn, project: Project = Depends(owned_project), user: User = Depends(current_user),
           db: Session = Depends(get_db), runner: WorkflowRunner = Depends(get_runner)):
    if project.status in ProjectStatus.TERMINAL:
        raise _conflict("The workflow has already finished")
    _record(db, project, user, "cancel", "requested", body.comment)
    if runner.is_active(project.id):
        project.control = "cancel"  # the worker stops at the next step boundary
    else:
        project.status = ProjectStatus.CANCELLED
        project.status_reason = body.comment or "Cancelled by user"
    db.commit()
    return _detail(db, project, runner)


# --- inspection ------------------------------------------------------------------------------

@router.get("/{project_id}/tasks", response_model=list[TaskOut])
def tasks(project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    return db.scalars(select(Task).where(Task.project_id == project.id).order_by(Task.order_index, Task.id)).all()


@router.get("/{project_id}/agent-runs", response_model=list[AgentRunOut])
def agent_runs(project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    return db.scalars(select(AgentRun).where(AgentRun.project_id == project.id).order_by(AgentRun.id)).all()


@router.get("/{project_id}/tests", response_model=list[TestRunOut])
def test_runs(project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    return db.scalars(select(TestRun).where(TestRun.project_id == project.id).order_by(TestRun.id)).all()


@router.get("/{project_id}/findings", response_model=list[FindingOut])
def findings(project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    return db.scalars(select(ReviewFinding).where(ReviewFinding.project_id == project.id).order_by(ReviewFinding.round, ReviewFinding.id)).all()


@router.get("/{project_id}/approvals", response_model=list[ApprovalOut])
def approvals(project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    return db.scalars(select(Approval).where(Approval.project_id == project.id).order_by(Approval.id)).all()


@router.get("/{project_id}/artifacts", response_model=list[ArtifactOut])
def artifacts(project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    return db.scalars(select(Artifact).where(Artifact.project_id == project.id).order_by(Artifact.id)).all()


@router.get("/{project_id}/events", response_model=list[EventOut])
def events(after: int = 0, limit: int = Query(500, le=2000), project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    return db.scalars(select(WorkflowEvent).where(WorkflowEvent.project_id == project.id, WorkflowEvent.id > after)
                      .order_by(WorkflowEvent.id).limit(limit)).all()


@router.get("/{project_id}/events/stream")
async def event_stream(request: Request, after: int = 0, project: Project = Depends(owned_project)):
    """Server-Sent Events: workflow events as they are recorded, plus project status changes."""
    project_id = project.id

    def poll(last_id: int) -> tuple[list[dict], str]:
        with SessionLocal() as db:
            rows = db.scalars(select(WorkflowEvent).where(WorkflowEvent.project_id == project_id, WorkflowEvent.id > last_id)
                              .order_by(WorkflowEvent.id).limit(200)).all()
            status_ = db.get(Project, project_id).status
            return [EventOut.model_validate(r).model_dump(mode="json") for r in rows], status_

    async def generate():
        last_id, last_status, idle = after, None, 0
        yield "retry: 2000\n\n"
        while not await request.is_disconnected():
            rows, status_ = await run_in_threadpool(poll, last_id)
            for row in rows:
                last_id = row["id"]
                yield f"id: {row['id']}\nevent: workflow\ndata: {json.dumps(row)}\n\n"
            if status_ != last_status:
                last_status = status_
                yield f"event: status\ndata: {json.dumps({'status': status_})}\n\n"
            idle = 0 if rows else idle + 1
            if idle and idle % 30 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{project_id}/metrics")
def metrics(project: Project = Depends(owned_project), db: Session = Depends(get_db)):
    return project_metrics(db, project)


# --- code workspace (16.6) -------------------------------------------------------------------

def _workspace(project: Project) -> Workspace:
    if not project.workspace_path or not Path(project.workspace_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "The workspace has not been created yet")
    return Workspace(Path(project.workspace_path))


@router.get("/{project_id}/files")
def files(project: Project = Depends(owned_project)):
    if not project.workspace_path or not Path(project.workspace_path).exists():
        return []
    ws = Workspace(Path(project.workspace_path))
    return [{"path": rel, "size": (ws.root / rel).stat().st_size} for rel in ws.list_files()]


@router.get("/{project_id}/files/content", response_class=PlainTextResponse)
def file_content(path: str, project: Project = Depends(owned_project)):
    ws = _workspace(project)
    try:
        return ws.read(path)
    except PathViolation as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found") from None
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Binary file") from None


@router.get("/{project_id}/commits")
def commits(project: Project = Depends(owned_project)):
    if not project.workspace_path or not Path(project.workspace_path).exists():
        return []
    return GitRepo(Path(project.workspace_path)).log()


@router.get("/{project_id}/commits/{sha}", response_class=PlainTextResponse)
def commit_diff(sha: str, project: Project = Depends(owned_project)):
    try:
        return GitRepo(_workspace(project).root).show(sha)
    except GitError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


# --- delivery (FR-16) ------------------------------------------------------------------------

@router.get("/{project_id}/delivery")
def delivery(project: Project = Depends(owned_project)):
    report = build_report(project.id)
    return {**report, "markdown": render_markdown(report),
            "package": Path(project.package_path).name if project.package_path and Path(project.package_path).is_file() else None}


@router.get("/{project_id}/package")
def download_package(project: Project = Depends(owned_project)):
    if not project.package_path or not Path(project.package_path).is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No delivery package yet")
    path = Path(project.package_path).resolve()
    if not path.is_relative_to(get_settings().packages_dir.resolve()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No delivery package yet")
    return FileResponse(path, media_type="application/zip", filename=path.name)
