"""FastAPI application entry point: `uvicorn app.main:app`."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import auth, integrations, projects, system
from app.config import get_settings
from app.db import init_db
from app.integrations.webhooks import WebhookDispatcher
from app.orchestrator.runner import WorkflowRunner, recover_interrupted

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("devflow")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    recovered = recover_interrupted()
    if recovered:
        log.warning("Marked %s interrupted workflow(s) as resumable", recovered)
    app.state.runner = WorkflowRunner(settings)
    app.state.webhooks = WebhookDispatcher(settings)
    app.state.webhooks.start()
    log.info("DevFlow AI %s ready (provider=%s, executor=%s)", __version__, settings.llm_provider, settings.executor)
    yield
    app.state.webhooks.stop()
    app.state.runner.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="DevFlow AI", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(integrations.router)
    return app


app = create_app()
