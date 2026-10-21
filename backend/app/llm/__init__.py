"""Model access behind a replaceable provider interface (spec 8.7)."""

from __future__ import annotations

import os

from app.config import Settings
from app.llm.base import LLMClient, LLMError, LLMResult, Usage


def create_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "anthropic":
        from app.llm.anthropic_client import AnthropicClient

        return AnthropicClient(
            model=settings.llm_model,
            effort=settings.llm_effort,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_seconds,
        )
    from app.llm.mock_client import MockClient

    return MockClient(delay_seconds=float(os.getenv("DEVFLOW_MOCK_DELAY", "0.4")))


__all__ = ["LLMClient", "LLMError", "LLMResult", "Usage", "create_llm_client"]
