"""Claude API provider using the official Anthropic SDK with JSON-schema structured outputs."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import anthropic
from pydantic import BaseModel, ValidationError

from app.llm.base import LLMClient, LLMError, LLMResult, Usage, estimate_cost, strict_json_schema

log = logging.getLogger(__name__)

MAX_VALIDATION_RETRIES = 1


class AnthropicClient(LLMClient):
    provider = "anthropic"

    def __init__(self, *, model: str, effort: str, max_tokens: int, timeout: float):
        # Credentials resolve from the environment (ANTHROPIC_API_KEY or an `ant auth login` profile).
        self._client = anthropic.Anthropic(timeout=timeout, max_retries=3)
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens

    def generate(
        self,
        *,
        role: str,
        system: str,
        prompt: str,
        output_model: type[BaseModel],
        meta: dict[str, Any] | None = None,
    ) -> LLMResult:
        schema = strict_json_schema(output_model)
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        usage = Usage()
        started = time.monotonic()
        served_by = self.model

        for attempt in range(1, MAX_VALIDATION_RETRIES + 2):
            message = self._stream(system=system, messages=messages, schema=schema)
            served_by = message.model or self.model
            usage.add(_usage_of(message))

            if message.stop_reason == "refusal":
                category = getattr(getattr(message, "stop_details", None), "category", None)
                raise LLMError(f"{role} request was declined by the model (category: {category})")
            if message.stop_reason == "max_tokens":
                raise LLMError(f"{role} output exceeded max_tokens={self.max_tokens}; reduce task scope", retryable=False)

            text = next((b.text for b in message.content if b.type == "text"), "")
            try:
                output = output_model.model_validate(json.loads(text))
            except (json.JSONDecodeError, ValidationError) as exc:
                if attempt > MAX_VALIDATION_RETRIES:
                    raise LLMError(f"{role} output failed schema validation: {exc}") from exc
                log.warning("%s output invalid (attempt %s): %s", role, attempt, exc)
                # Append-only history: keep the assistant turn intact (including thinking blocks).
                messages.append({"role": "assistant", "content": message.content})
                messages.append({
                    "role": "user",
                    "content": f"Your previous output was rejected by validation:\n{exc}\nReturn a corrected, complete JSON object.",
                })
                continue

            latency_ms = int((time.monotonic() - started) * 1000)
            return LLMResult(
                output=output,
                usage=usage,
                model=served_by,
                latency_ms=latency_ms,
                cost_usd=estimate_cost(served_by, usage),
                attempts=attempt,
            )

        raise LLMError(f"{role} produced no valid output")  # pragma: no cover

    def _stream(self, *, system: str, messages: list[dict[str, Any]], schema: dict[str, Any]):
        try:
            with self._client.beta.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=messages,
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                cache_control={"type": "ephemeral"},
                # Route policy declines to a fallback model chosen by the API.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                return stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise LLMError("Anthropic authentication failed; set ANTHROPIC_API_KEY or run `ant auth login`") from exc
        except anthropic.BadRequestError as exc:
            raise LLMError(f"Anthropic rejected the request: {exc.message}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Anthropic rate limit reached after retries", retryable=True) from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}", retryable=exc.status_code >= 500) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the Anthropic API", retryable=True) from exc


def _usage_of(message) -> Usage:
    u = message.usage
    return Usage(
        input_tokens=u.input_tokens or 0,
        output_tokens=u.output_tokens or 0,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )
