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
from contextlib import contextmanager
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from battalion.execution import record_llm_call, record_stream_observation
from battalion.llm.configuration import (
    ModelDiversityError, NodeLLMConfig, validate_model_diversity,
)
from battalion.llm.cost_policy import active_cost_policy, nonzero_cost_violation
from battalion.state.models import CostSource, LLMCallCost


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


class InferenceIdentityContradiction(InfraFailure):
    """Provider evidence disproves an admitted Driver/Reviewer distinction."""


class CostPolicyViolation(InfraFailure):
    """A provider reported paid inference inside a zero-cost run."""


def build_node_configs(raw: dict[str, dict]) -> dict[str, NodeLLMConfig]:
    """Build per-node LLM configs from plain config data (e.g. loaded from
    a config file). Nodes never hardcode their own model.
    
    Raises:
        ModelDiversityError: If driver and reviewer models are the same.
    """
    configs = {node: NodeLLMConfig(**cfg) for node, cfg in raw.items()}
    
    validate_model_diversity(configs)
    
    return configs


_litellm_silenced = False
_streaming_logging_lock = Lock()
_active_streaming_calls = 0
_previous_streaming_logging_setting: bool | None = None


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


@contextmanager
def _without_litellm_streaming_logging():
    """Prevent LiteLLM's optional per-chunk callback worker from leaking.

    Battalion already owns live observation and durable LLM-call cost evidence.
    LiteLLM's independent streaming logging path therefore adds no product
    evidence, but LiteLLM 1.98 can create unawaited ``async_success_handler``
    coroutines on Windows while that worker drains.  The provider stream itself
    remains enabled.  Preserve the caller's global LiteLLM setting and keep it
    disabled until every concurrent Battalion stream has finished.
    """
    global _active_streaming_calls, _previous_streaming_logging_setting
    import litellm

    with _streaming_logging_lock:
        if _active_streaming_calls == 0:
            _previous_streaming_logging_setting = litellm.disable_streaming_logging
            litellm.disable_streaming_logging = True
        _active_streaming_calls += 1
    try:
        yield
    finally:
        with _streaming_logging_lock:
            _active_streaming_calls -= 1
            if _active_streaming_calls == 0:
                litellm.disable_streaming_logging = _previous_streaming_logging_setting
                _previous_streaming_logging_setting = None


def _streamed_response(
    full_content: str,
    model: str,
    usage: Any = None,
    response_cost: float | None = None,
    response_model: str | None = None,
    routed_provider: str | None = None,
    routed_model: str | None = None,
) -> dict[str, Any]:
    """Assemble a litellm-shaped response dict from accumulated streamed
    text, so existing extract_content / extract_files helpers (which handle
    dict responses) work unchanged after a streaming call."""
    response = {
        "choices": [{"message": {"content": full_content}}],
        "model": model,
    }
    if usage is not None:
        response["usage"] = usage
    hidden: dict[str, Any] = {"streamed_response": True}
    if response_cost is not None:
        hidden["response_cost"] = response_cost
    if response_model is not None:
        hidden["response_model"] = response_model
    if routed_provider is not None:
        hidden["routed_provider"] = routed_provider
    if routed_model is not None:
        hidden["routed_model"] = routed_model
    response["_hidden_params"] = hidden
    return response


def _value(container: Any, key: str, default: Any = None) -> Any:
    if isinstance(container, dict):
        return container.get(key, default)
    return getattr(container, key, default)


def _header_value(headers: Any, name: str) -> Any:
    """Read a case-insensitive response header from common client shapes."""
    if not headers:
        return None
    if isinstance(headers, dict):
        return next((value for key, value in headers.items() if key.lower() == name.lower()), None)
    if hasattr(headers, "get"):
        return headers.get(name) or headers.get(name.lower())
    return None


def _text_evidence(value: Any) -> str | None:
    """Keep bounded typed evidence; malformed or absent provider data is absent."""
    return value.strip() if isinstance(value, str) and value.strip() else None


def _response_metadata(response: Any) -> dict[str, Any]:
    """Return only provider-supplied identity metadata, never inferred labels."""
    hidden = _value(response, "_hidden_params") or {}
    headers = (
        _value(response, "headers")
        or _value(response, "response_headers")
        or _value(hidden, "headers")
        or _value(hidden, "response_headers")
    )
    routed_via = _header_value(headers, "X-Routed-Via")
    routed_model = _header_value(headers, "X-Routed-Model")
    # Some OpenAI-compatible routers expose these fields in their response
    # body instead of headers.  They are evidence only when explicitly sent.
    response_model = (
        _value(hidden, "response_model")
        if _value(hidden, "streamed_response")
        else _value(response, "model")
    )
    return {
        "response_model": _text_evidence(response_model),
        "routed_provider": _text_evidence(
            routed_via or _value(response, "routed_provider") or _value(hidden, "routed_provider")
        ),
        "routed_model": _text_evidence(
            routed_model or _value(response, "routed_model") or _value(hidden, "routed_model")
        ),
    }


def _record_response_cost(response: Any, config: NodeLLMConfig) -> str | None:
    usage = _value(response, "usage") or {}
    hidden = _value(response, "_hidden_params") or {}
    input_tokens = _value(usage, "prompt_tokens", _value(usage, "input_tokens", 0))
    output_tokens = _value(
        usage, "completion_tokens", _value(usage, "output_tokens", 0)
    )
    provider_cost = _value(usage, "cost")
    estimated_cost = _value(
        hidden,
        "response_cost",
        _value(response, "response_cost"),
    )
    cost = provider_cost if provider_cost is not None else estimated_cost
    currency = _value(
        usage,
        "cost_currency",
        _value(hidden, "response_cost_currency", _value(response, "cost_currency")),
    )
    source = (
        CostSource.PROVIDER_REPORTED
        if provider_cost is not None
        else CostSource.ESTIMATED
        if estimated_cost is not None
        else CostSource.UNKNOWN
    )
    metadata = _response_metadata(response)
    response_model = metadata["response_model"]
    policy = active_cost_policy()
    violation = nonzero_cost_violation(
        policy,
        provider_reported=source is CostSource.PROVIDER_REPORTED,
        cost_is_nonzero=cost is not None and float(cost) > 0,
    )
    record_llm_call(
        LLMCallCost(
            call_id=f"llm-{uuid4()}",
            model=response_model or config.model,
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            cost=str(cost) if cost is not None else None,
            cost_currency=(currency or "USD") if cost is not None else None,
            cost_source=source,
            requested_model=config.model,
            response_model=response_model,
            backend=config.backend,
            endpoint_url=config.endpoint_url,
            inference_location=config.inference_location,
            routed_provider=metadata["routed_provider"],
            routed_model=metadata["routed_model"],
            identity_contradiction=violation,
            cost_policy=policy,
        )
    )
    return violation


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
    params = config.request_params()
    params["stream"] = True
    params.setdefault("stream_options", {"include_usage": True})
    pieces: list[str] = []
    usage = None
    response_cost = None
    response_model = None
    routed_provider = None
    routed_model = None
    with _without_litellm_streaming_logging():
        stream = completion_fn(
            model=config.model,
            messages=messages,
            temperature=config.temperature,
            **params,
        )
        for chunk in stream:
            response_model = _text_evidence(getattr(chunk, "model", None)) or response_model
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                usage = chunk_usage
                usage_cost = _value(chunk_usage, "cost")
                if usage_cost is not None:
                    response_cost = usage_cost
            hidden = getattr(chunk, "_hidden_params", None) or {}
            chunk_cost = _value(hidden, "response_cost")
            if chunk_cost is not None:
                response_cost = chunk_cost
            headers = (
                getattr(chunk, "headers", None)
                or getattr(chunk, "response_headers", None)
                or _value(hidden, "headers")
                or _value(hidden, "response_headers")
            )
            routed_provider = _text_evidence(_header_value(headers, "X-Routed-Via")) or routed_provider
            routed_model = _text_evidence(_header_value(headers, "X-Routed-Model")) or routed_model
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
                record_stream_observation("reasoning", reasoning)
            content = getattr(delta, "content", None)
            if content:
                on_stream({"type": "token", "content": content})
                record_stream_observation("token", content)
                pieces.append(content)
    return _streamed_response(
        "".join(pieces), config.model, usage=usage, response_cost=response_cost,
        response_model=response_model, routed_provider=routed_provider, routed_model=routed_model,
    )


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
                response = _completion_streaming(
                    config, messages, completion_fn, on_stream
                )
            else:
                response = completion_fn(
                    model=config.model,
                    messages=messages,
                    temperature=config.temperature,
                    **config.request_params(),
                )
            violation = _record_response_cost(response, config)
            if violation:
                raise CostPolicyViolation(node_name, config.model, attempt, RuntimeError(violation))
            return response
        except CostPolicyViolation:
            raise
        except Exception as exc:  # noqa: BLE001 - deliberately broad: any
            # failure here (network, provider error, timeout) should count
            # toward the retry budget rather than crash the node outright.
            # Provider errors can contain the request's bearer token or headers.
            last_error = (
                RuntimeError(f"{type(exc).__name__}: configured inference target failed")
                if config.endpoint_url or config.api_key_env else exc
            )
            if attempt < max_attempts:
                sleep_fn(backoff_seconds)

    raise InfraFailure(
        node_name=node_name,
        model=config.model,
        attempts=max_attempts,
        last_error=last_error,
    )
