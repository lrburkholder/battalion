"""`battalion setup` orchestration (BTN-15).

Turns the manual config dance — provider prefixes, API keys, model
diversity — into a single command that ends with a *working*
battalion.config.yaml:

  1. Reads the existing config (if any) so only the specified changes land.
  2. Resolves a model for each node (CLI override > existing > prompt > default).
  3. Normalizes each model to its explicit "provider/model" form via litellm's
     own provider detection, so "gpt-4o-mini" becomes "openai/gpt-4o-mini".
  4. Enforces BTN-14 model diversity (Driver != Reviewer) automatically.
  5. Verifies an API key is available for every provider that needs one.
  6. Validates live connectivity to each configured model with a minimal
     completion, and refuses to save until every check passes.

Nothing here is wired to a specific terminal UI: prompting is injectable so
the whole flow is testable without a TTY. The CLI command in cli.py is the
thin adapter.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

from battalion.config import DEFAULT_CONFIG_PATH, save_config
from battalion.disclosure import DATA_HANDLING_URL
from battalion.llm.litellm_client import ModelDiversityError, _silence_litellm_output
from battalion.llm.configuration import (
    InferenceConfigurationError, NodeLLMConfig, validate_model_diversity,
)


class ProviderNotDetected(Exception):
    """litellm could not map the model string to a known provider."""


class MissingApiKey(Exception):
    """A provider requires an API key but none is present in the environment."""


class ConnectivityCheckFailed(Exception):
    """A minimal completion against a configured model did not succeed."""


def validate_openai_compatible_model_catalog(
    config: NodeLLMConfig,
    api_key: str | None,
    *,
    opener: Callable[..., Any] = urlopen,
) -> None:
    """Confirm an OpenAI-compatible router advertises the requested model.

    This is deliberately a setup concern, rather than a role or graph concern:
    a router is just an endpoint. The bearer credential is resolved by
    ``NodeLLMConfig`` and used only for this request; errors do not echo it.
    """
    if not config.endpoint_url:
        raise InferenceConfigurationError("An OpenAI-compatible catalog check requires endpoint_url")
    requested_model = config.model.split("/", 1)[-1]
    request = Request(
        f"{config.endpoint_url.rstrip('/')}/models",
        headers={
            "Accept": "application/json",
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        },
    )
    try:
        with opener(request, timeout=10) as response:
            payload = json.load(response)
        entries = payload.get("data") if isinstance(payload, dict) else None
        model_ids = {
            entry.get("id") for entry in entries or []
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        }
    except (OSError, URLError, ValueError, TypeError, json.JSONDecodeError):
        raise ConnectivityCheckFailed(
            "Model catalog check failed for configured inference target. "
            "Check endpoint availability and credentials."
        ) from None
    if requested_model not in model_ids:
        raise ConnectivityCheckFailed(
            f"Configured model {requested_model!r} is not available from the endpoint catalog."
        )


# Sensible defaults for a first-run config with no existing file. All four
# default to OpenAI so a single OPENAI_API_KEY gets a working config; Driver
# and Reviewer use different models to satisfy BTN-14 model diversity.
DEFAULT_MODELS: dict[str, str] = {
    "architect": "openai/gpt-4o-mini",
    "driver": "openai/gpt-4o-mini",
    "reviewer": "openai/gpt-4o",
    "refactorer": "openai/gpt-4o-mini",
}

# Candidates used to auto-correct the Reviewer when its (default) model would
# otherwise collide with the Driver's, keeping BTN-14 diversity automatic.
_REVIEWER_FALLBACKS = (
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "anthropic/claude-3-5-sonnet-20241022",
    "mistral/mistral-medium-latest",
    "google/gemini-1.5-flash",
)

NODE_ORDER = ["architect", "driver", "reviewer", "refactorer"]

# provider -> candidate env var names, in priority order. Fallback for
# unlisted providers: f"{PROVIDER.upper()}_API_KEY".
_PROVIDER_ENV_VARS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "azure": ("AZURE_API_KEY",),
    "cohere": ("COHERE_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "together_ai": ("TOGETHERAI_API_KEY", "TOGETHER_AI_TOKEN"),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_TOKEN"),
    "perplexity": ("PERPLEXITY_API_KEY",),
    "huggingface": ("HUGGINGFACE_API_KEY",),
    "replicate": ("REPLICATE_API_KEY",),
    "bedrock": (),
}


def provider_env_vars(provider: str) -> tuple[str, ...]:
    """Candidate env var names for a provider's API key (empty if none)."""
    if provider in _PROVIDER_ENV_VARS:
        return _PROVIDER_ENV_VARS[provider]
    return (f"{provider.upper().replace('-', '_')}_API_KEY",)


def api_key_for_provider(provider: str) -> str | None:
    """Return the first set key from the provider's candidate env vars."""
    for var in provider_env_vars(provider):
        value = os.environ.get(var)
        if value:
            return value
    return None


def detect_provider(model: str) -> tuple[str, str]:
    """Resolve a model string to (model_name, provider) via litellm.

    Handles the provider-prefix concern from BTN-15's motivation: litellm's
    own detection turns both "gpt-4o-mini" and "openai/gpt-4o-mini" into
    ("gpt-4o-mini", "openai"), which the caller re-prefixes consistently.
    """
    import litellm

    _silence_litellm_output()
    try:
        resolved_model, provider, _api_key, _api_base = litellm.get_llm_provider(model)
    except Exception as exc:  # noqa: BLE001 - litellm raises various provider errors
        raise ProviderNotDetected(
            f"Could not determine the provider for model {model!r}: {exc}\n"
            "Set a provider prefix, e.g. openai/gpt-4o-mini, anthropic/claude-3-5-sonnet, "
            "mistral/mistral-medium-latest."
        ) from exc
    return resolved_model, provider


def validate_connectivity(
    model: str,
    api_key: str | None = None,
    completion_fn: Callable[..., Any] | None = None,
    config: NodeLLMConfig | None = None,
) -> None:
    """Confirm a model is reachable with a working key.

    Issues a minimal one-token completion. `completion_fn` is injectable so
    tests can fake the provider round-trip; it must accept the same kwargs a
    litellm completion call does. Raises ConnectivityCheckFailed on any error.
    """
    import litellm

    _silence_litellm_output()
    fn = completion_fn or litellm.completion
    target = config or NodeLLMConfig(model=model)
    kwargs: dict[str, Any] = {
        **target.request_params(),
        "model": model,
        "temperature": target.temperature,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "caching": False,
    }
    if "max_completion_tokens" in kwargs:
        kwargs["max_completion_tokens"] = 1
    if api_key and "api_key" not in kwargs:
        kwargs["api_key"] = api_key
    try:
        fn(**kwargs)
    except Exception as exc:  # noqa: BLE001 - any provider/auth error is a failure
        raise ConnectivityCheckFailed(
            f"Connectivity check failed for {model} ({type(exc).__name__}). "
            "Check endpoint availability, model availability, and credentials."
        ) from None


def _normalized_model_string(model: str, provider: str) -> str:
    """Rebuild the explicit "provider/model" string for storage."""
    return f"{provider}/{model}"


def _pick_reviewer_fallback(driver_model: str) -> str:
    """Choose a distinct default Reviewer model for the driver's model."""
    for candidate in _REVIEWER_FALLBACKS:
        try:
            name, provider = detect_provider(candidate)
        except ProviderNotDetected:
            continue
        if _normalized_model_string(name, provider) != driver_model:
            return candidate
    raise ModelDiversityError(
        f"Driver uses model '{driver_model}' and no distinct Reviewer model "
        "could be chosen automatically. Provide one with --model-reviewer."
    )


def parse_role_options(options: dict[str, list[str]]) -> dict[str, dict[str, Any]]:
    """Parse repeatable CLI ROLE=VALUE options at the setup boundary."""
    result: dict[str, dict[str, Any]] = {}
    for field, entries in options.items():
        for entry in entries:
            role, separator, value = entry.partition("=")
            if not separator or role not in NODE_ORDER:
                raise InferenceConfigurationError("Target options require ROLE=VALUE for architect, driver, reviewer, or refactorer")
            if field == "keyless":
                if value not in {"true", "false", "auto"}:
                    raise InferenceConfigurationError("keyless must be true, false, or auto")
                value = {"true": True, "false": False, "auto": None}[value]
            else:
                value = value or None
            result.setdefault(role, {})[field] = value
    return result


def run_setup(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    model_overrides: dict[str, str] | None = None,
    validate: bool = True,
    completion_fn: Callable[..., Any] | None = None,
    prompt: Callable[[str, str], str] | None = None,
    echo: Callable[[str], None] = print,
    existing_yaml: dict[str, Any] | None = None,
    node_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Preserve, resolve, and validate complete per-role inference targets.

    Model flags override node_overrides, which override existing configuration.
    Secret values are resolved only for requests and never added to saved data.
    No configuration is written until every preflight and requested live check
    succeeds. Extra configured roles are preserved and validated too.
    """
    echo(
        "Data handling: setup validation sends a live provider request; runs may "
        f"send project context. Read {DATA_HANDLING_URL} before continuing."
    )
    path = Path(config_path)
    if existing_yaml is None and path.exists():
        import yaml
        try:
            existing_yaml = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            raise InferenceConfigurationError("Configuration must be valid YAML") from None
    existing_yaml = deepcopy(existing_yaml) if existing_yaml is not None else {}
    if not isinstance(existing_yaml, dict) or not isinstance(existing_yaml.get("models", {}), dict):
        raise InferenceConfigurationError("Configuration and models must be mappings")
    existing_models = existing_yaml.get("models", {})
    overrides = {k: v for k, v in (model_overrides or {}).items() if v}
    node_overrides = node_overrides or {}
    if (set(overrides) | set(node_overrides)) - set(NODE_ORDER):
        raise InferenceConfigurationError("Unknown setup role override")
    roles = list(dict.fromkeys([*NODE_ORDER, *existing_models]))
    configs: dict[str, NodeLLMConfig] = {}
    for node in roles:
        data = deepcopy(existing_models.get(node, existing_models.get("default", {})))
        if not isinstance(data, dict):
            raise InferenceConfigurationError("Each model configuration must be a mapping")
        changes = deepcopy(node_overrides.get(node, {}))
        if not isinstance(changes, dict):
            raise InferenceConfigurationError("Each role override must be a mapping")
        requested = overrides.get(node) or changes.get("model") or data.get("model")
        if not requested:
            default = DEFAULT_MODELS.get(node)
            if default is None:
                raise InferenceConfigurationError(f"Missing model for {node}")
            requested = prompt(f"Model for {node} (e.g. gpt-4o-mini)", default) if prompt else default
            requested = requested or default
        requested = NodeLLMConfig(model=requested).model
        name, provider = detect_provider(requested)
        normalized = _normalized_model_string(name, provider)
        if "model" in data:
            old_name, old_provider = detect_provider(data["model"])
            if normalized != _normalized_model_string(old_name, old_provider):
                data.pop("canonical_model_family", None)
        if "endpoint_url" in changes and isinstance(data.get("extra_params"), dict):
            data["extra_params"].pop("api_base", None)
        data.update(changes)
        data["model"] = normalized
        if prompt is not None and node not in node_overrides:
            endpoint = prompt(f"Endpoint base URL for {node} (blank uses provider default)", data.get("endpoint_url") or data.get("extra_params", {}).get("api_base", ""))
            if endpoint:
                data["endpoint_url"] = endpoint
                data["inference_location"] = prompt(f"Inference location for {node}: local, remote, unknown (an operator assertion)", data.get("inference_location", "unknown"))
                data["api_key_env"] = prompt(f"Credential environment variable for {node} (name only; blank for automatic/keyless)", data.get("api_key_env") or "") or None
                if node in {"driver", "reviewer"}:
                    data["canonical_model_family"] = prompt(f"Concrete canonical model family for {node} (same across providers and quantizations)", data.get("canonical_model_family") or "") or None
        try:
            configs[node] = NodeLLMConfig(**data)
        except TypeError:
            raise InferenceConfigurationError(f"Unsupported configuration fields for {node}") from None

    reviewer_explicit = (
        "reviewer" in overrides or "reviewer" in existing_models
        or "reviewer" in node_overrides or "default" in existing_models
    )
    if configs["driver"].model == configs["reviewer"].model and not reviewer_explicit:
        # Preserve the legacy first-run default correction only. Never move an
        # endpoint-configured role to a remote default to repair diversity.
        if not configs["driver"].endpoint_url and not configs["driver"].canonical_model_family:
            fallback = _pick_reviewer_fallback(configs["driver"].model)
            configs["reviewer"] = NodeLLMConfig(model=fallback)
            echo(f"Reviewer would use the same model as Driver; set to {fallback} to satisfy model diversity.")
    validate_model_diversity(configs)

    keys: dict[str, str | None] = {}
    for node, target in configs.items():
        params = target.request_params()
        key = params.get("api_key")
        if key is None:
            key = api_key_for_provider(target.provider)
            if key is None:
                hint = " or ".join(provider_env_vars(target.provider))
                raise MissingApiKey(f"Provider '{target.provider}' requires an API key. Set {hint} in the environment and rerun setup.")
        keys[node] = key

    if validate:
        validated: list[tuple[Any, ...]] = []
        for node, target in configs.items():
            # Equality includes model, endpoint, auth reference, and all request
            # settings. Secrets themselves are never used as persisted identity.
            identity = target.validation_identity()
            if identity in validated:
                continue
            endpoint = target.endpoint_url or "provider default"
            echo(f"Validating connectivity: {target.model} at {endpoint} (inference: {target.inference_location}) ...")
            if (target.backend or "").casefold() == "freellmapi":
                validate_openai_compatible_model_catalog(target, keys[node])
            validate_connectivity(target.model, api_key=keys[node], completion_fn=completion_fn, config=target)
            validated.append(identity)
            echo(f"  ok: {target.model}")

    models = {node: asdict(target) for node, target in configs.items()}
    save_config(models, config_path=path, existing=existing_yaml)
    return models
