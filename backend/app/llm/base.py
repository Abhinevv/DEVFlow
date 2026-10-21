"""Provider-neutral LLM interface. Agents depend only on this module."""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


class LLMError(RuntimeError):
    """The model call failed or produced no usable output."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens


@dataclass
class LLMResult:
    output: BaseModel
    usage: Usage
    model: str
    latency_ms: int
    cost_usd: float
    attempts: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


class LLMClient(ABC):
    provider: str
    model: str

    @abstractmethod
    def generate(
        self,
        *,
        role: str,
        system: str,
        prompt: str,
        output_model: type[BaseModel],
        meta: dict[str, Any] | None = None,
    ) -> LLMResult:
        """Run one structured generation and return a validated instance of output_model.

        `meta` carries orchestration context (task key, attempt number). Real providers ignore it;
        the mock provider uses it to pick a scripted response.
        """


# Pricing per million tokens (USD), first-party Claude API rates.
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


def estimate_cost(model: str, usage: Usage) -> float:
    rates = PRICING.get(model)
    if rates is None:
        return 0.0
    input_rate, output_rate = rates
    # Cache writes bill at 1.25x input; cache reads at 0.1x input.
    cost = (
        usage.input_tokens * input_rate
        + usage.cache_write_tokens * input_rate * 1.25
        + usage.cache_read_tokens * input_rate * 0.1
        + usage.output_tokens * output_rate
    )
    return round(cost / 1_000_000, 6)


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema adapted for structured outputs: refs inlined, every property required,
    no additional properties, and no keywords the API's schema subset rejects."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                return resolve(copy.deepcopy(defs[name]))
            out = {k: resolve(v) for k, v in node.items() if k not in ("default", "title")}
            if out.get("type") == "object" and "properties" in out:
                out["required"] = list(out["properties"].keys())
                out["additionalProperties"] = False
            return out
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)
