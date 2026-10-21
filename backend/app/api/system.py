from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import __version__
from app.config import get_settings
from app.db import get_db
from app.metrics import aggregate_metrics
from app.models import Project, User
from app.security import current_user
from app.templates import list_templates
from app.tools.executor import DockerExecutor

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@router.get("/system")
def system_info(user: User = Depends(current_user)):
    s = get_settings()
    docker = DockerExecutor.available() if s.executor in ("auto", "docker") else False
    executor = "docker" if docker else ("local" if s.allow_local_execution and s.executor != "docker" else "unavailable")
    return {
        "version": __version__,
        "llm_provider": s.llm_provider,
        "llm_model": s.llm_model if s.llm_provider == "anthropic" else "mock-scripted",
        "llm_effort": s.llm_effort,
        "executor": executor,
        "isolated_execution": docker,
        "limits": {"max_debug_attempts": s.max_debug_attempts, "max_review_rounds": s.max_review_rounds,
                   "max_cost_usd": s.max_cost_usd, "max_workflow_seconds": s.max_workflow_seconds},
    }


@router.get("/templates")
def templates(user: User = Depends(current_user)):
    return [t.public() for t in list_templates()]


@router.get("/metrics")
def metrics(user: User = Depends(current_user), db: Session = Depends(get_db)):
    projects = list(db.scalars(select(Project).where(Project.owner_id == user.id)))
    return {
        "multi_agent": aggregate_metrics(db, [p for p in projects if p.mode == "multi_agent"]),
        "single_agent": aggregate_metrics(db, [p for p in projects if p.mode == "single_agent"]),
    }
