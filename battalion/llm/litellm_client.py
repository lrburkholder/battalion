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


class ModelDiversityError(Exception):
    """Raised when Reviewer and Driver are configured with the same model.
    
    This enforces BTN-14: Model-diversity constraint between Driver and Reviewer
    to prevent code from being written and reviewed by the same model.
    """
    pass


def build_node_configs(raw: dict[str, dict]) -> dict[str, NodeLLMConfig]:
    """Build per-node LLM configs from plain config data (e.g. loaded from
    a config file). Nodes never hardcode their own model.
    
    Raises:
        ModelDiversityError: If driver and reviewer models are the same.
    """
    configs = {node: NodeLLMConfig(**cfg) for node, cfg in raw.items()}
    
    # BTN-14: Enforce model diversity between Driver and Reviewer
    # Only enforce when both are explicitly configured (not just falling back to default)
    # This prevents blocking valid configs where both use a sensible default
    driver_explicit = "driver" in raw
    reviewer_explicit = "reviewer" in raw
    
    if driver_explicit and reviewer_explicit:
        driver_config = configs["driver"]
        reviewer_config = configs["reviewer"]
        if driver_config.model == reviewer_config.model:
            raise ModelDiversityError(
                f"Driver and Reviewer cannot use the same model. "
                f"Both are configured with model '{driver_config.model}'. "
                f"Please configure different models for driver and reviewer nodes."
            )
    
    return configs


_litellm_silenced = False


def _silence_litellm_output() -> None:
    """Silence litellm's per-attempt error-handling prints.

    Battalion drives retries itself and surfaces the final provider error as
    an InfraFailure (routed to interrupt trigger #5) with a clear CLI pause
    message. litellm's own "Give Feedback / Get Help" and "Provider List"
    blocks (gated on litellm.suppress_debug_info) print once per failed
    attempt, which turns a retry storm into terminal spam. Idempotent; the
    flag is set just before each real completion so the print blocks are
    already suppressed by the time a call fails."""
    global _litellm_silenced
    if _litellm_silenced:
        return
    import litellm

    litellm.suppress_debug_info = True
    _litellm_silenced = True


def _default_completion_fn(**kwargs):  # pragma: no cover - thin passthrough
    import litellm

    _silence_litellm_output()
    return litellm.completion(**kwargs)


def _streamed_response(full_content: str) -> dict[str, Any]:
    """Assemble a litellm-shaped response dict from accumulated streamed
    text, so existing extract_content / extract_files helpers (which handle
    dict responses) work unchanged after a streaming call."""
    return {"choices": [{"message": {"content": full_content}}]}


def _completion_streaming(
    config: NodeLLMConfig,
    messages: list[dict[str, str]],
    completion_fn: Callable[..., Any],
    on_stream: Callable[[dict], None],
) -> dict[str, Any]:
    """Run completion_fn with stream=True, forwarding each chunk's content
    and reasoning deltas to on_stream, then return the assembled response.

    Event dicts forwarded to on_stream:
      {"type": "reasoning", "content": ...}  — model reasoning tokens
        (litellm's reasoning_content / reasoning delta fields, e.g. from
        DeepSeek-Reasoner or OpenAI o-series models)
      {"type": "token", "content": ...}      — regular generated content

    If a streamed chunk carries no content/reasoning delta (usage frames,
    finish-reason frames), it is skipped silently.
    """
    params = dict(config.extra_params)
    params["stream"] = True
    stream = completion_fn(
        model=config.model,
        messages=messages,
        temperature=config.temperature,
        **params,
    )
    pieces: list[str] = []
    for chunk in stream:
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        delta = choices[0].delta
        if delta is None:
            continue
        reasoning = getattr(delta, "reasoning_content", None)
        if not reasoning:
            reasoning = getattr(delta, "reasoning", None)
        if reasoning:
            on_stream({"type": "reasoning", "content": reasoning})
        content = getattr(delta, "content", None)
        if content:
            on_stream({"type": "token", "content": content})
            pieces.append(content)
    return _streamed_response("".join(pieces))


def call_llm(
    node_name: str,
    config: NodeLLMConfig,
    messages: list[dict[str, str]],
    completion_fn: Callable[..., Any] = _default_completion_fn,
    sleep_fn: Callable[[float], None] = time.sleep,
    backoff_seconds: float = 0.0,
    on_stream: Callable[[dict], None] | None = None,
) -> Any:
    """Call the configured model for a node, retrying config.max_retries
    times on failure. Raises InfraFailure once retries are exhausted.

    If on_stream is given, the completion runs in streaming mode and each
    streamed chunk (content + reasoning tokens) is forwarded to on_stream
    as an event dict; the fully-assembled response is still returned so the
    caller's content-extraction path is unchanged."""
    max_attempts = config.max_retries + 1
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            if on_stream is not None:
                return _completion_streaming(
                    config, messages, completion_fn, on_stream
                )
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
