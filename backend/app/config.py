"""Application settings, loaded from environment variables prefixed with DEVFLOW_ or backend/.env."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_prefix="DEVFLOW_",
        extra="ignore",
    )

    # Storage
    data_dir: Path = BACKEND_DIR / "data"
    database_url: str | None = None  # defaults to SQLite inside data_dir; set a postgresql+psycopg:// URL for PostgreSQL

    # Auth
    secret_key: str | None = None  # generated and persisted to data_dir on first start when unset
    token_ttl_hours: int = 24

    # Model access. "mock" is a deterministic scripted provider for demos and tests.
    llm_provider: Literal["anthropic", "mock"] = "mock"
    llm_model: str = "claude-opus-5-5"
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    llm_max_tokens: int = 64000
    llm_timeout_seconds: float = 900.0

    # Execution sandbox
    executor: Literal["auto", "docker", "local"] = "auto"
    allow_local_execution: bool = False  # local execution is NOT isolated; opt in explicitly
    sandbox_image: str = "devflow-sandbox:latest"
    exec_timeout_seconds: int = 120
    exec_memory_mb: int = 512
    exec_cpus: float = 1.0
    exec_pids_limit: int = 128
    max_output_bytes: int = 200_000

    # Workspace limits
    max_file_bytes: int = 200_000
    max_files_per_change: int = 40
    max_workspace_files: int = 300

    # Workflow limits (defaults; each project may override within these ceilings)
    max_debug_attempts: int = 3
    max_review_rounds: int = 1
    max_cost_usd: float = 5.0
    max_workflow_seconds: int = 3600
    max_safety_violations: int = 3

    # Integrations (outbound webhooks, e.g. to n8n)
    public_app_url: str = "http://localhost:5173"  # used for dashboard links in webhook payloads
    webhook_poll_seconds: float = 1.0
    webhook_timeout_seconds: float = 10.0
    webhook_max_attempts: int = 3

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'devflow.db').as_posix()}"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def packages_dir(self) -> Path:
        return self.data_dir / "packages"

    def resolved_secret_key(self) -> str:
        if self.secret_key:
            return self.secret_key
        key_file = self.data_dir / "secret_key"
        if key_file.exists():
            return key_file.read_text(encoding="utf-8").strip()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key = secrets.token_urlsafe(48)
        key_file.write_text(key, encoding="utf-8")
        return key


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
    settings.packages_dir.mkdir(parents=True, exist_ok=True)
    return settings
