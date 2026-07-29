"""Per-node LiteLLM client wrapper (BTN-3).

Each node gets its own model configuration, data-driven rather than
hardcoded, so switching a node's model is a config change, not a code
change. A failed call, after exhausting its configured retries, raises
InfraFailure — a distinct type callers can route to spec.md interrupt
trigger #5, rather than letting a raw provider exception propagate
unhandled.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


class InfraFailure(Exception):
    """Raised when an LLM call fails after exhausting configured retries.
    Distinct from a raw provider exception so graph wiring (BTN-7/BTN-8)
    can route this to interrupt trigger #5 rather than crashing."""

    def __init__(self, node_name: str, model: str, attempts: int, last_error: Exception):
        self.node_name = node_name
        self.model = model
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"LLM call for node '{node_name}' (model={model}) failed after "
            f"{attempts} attempt(s): {last_error}"
        )


@dataclass
class NodeLLMConfig:
    model: str
    max_retries: int = 2
    temperature: float = 0.0
    extra_params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError(
                f"max_retries must be >= 0, got {self.max_retries}"
            )


def build_node_configs(raw: dict[str, dict]) -> dict[str, NodeLLMConfig]:
    """Build per-node LLM configs from plain config data (e.g. loaded from
    a config file). Nodes never hardcode their own model."""
    return {node: NodeLLMConfig(**cfg) for node, cfg in raw.items()}


def _default_completion_fn(**kwargs):  # pragma: no cover - thin passthrough
    import litellm

    return litellm.completion(**kwargs)


def call_llm(
    node_name: str,
    config: NodeLLMConfig,
    messages: list[dict[str, str]],
    completion_fn: Callable[..., Any] = _default_completion_fn,
    sleep_fn: Callable[[float], None] = time.sleep,
    backoff_seconds: float = 0.0,
) -> Any:
    """Call the configured model for a node, retrying config.max_retries
    times on failure. Raises InfraFailure once retries are exhausted."""
    max_attempts = config.max_retries + 1
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return completion_fn(
                model=config.model,
                messages=messages,
                temperature=config.temperature,
                **config.extra_params,
            )
        except Exception as exc:  # noqa: BLE001 - deliberately broad: any
            # failure here (network, provider error, timeout) should count
            # toward the retry budget rather than crash the node outright.
            last_error = exc
            if attempt < max_attempts:
                sleep_fn(backoff_seconds)

    raise InfraFailure(
        node_name=node_name,
        model=config.model,
        attempts=max_attempts,
        last_error=last_error,
    )
