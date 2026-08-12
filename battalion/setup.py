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
  6. Validates live connectivity to each configured provider with a minimal
     completion, and refuses to save until every check passes.

Nothing here is wired to a specific terminal UI: prompting is injectable so
the whole flow is testable without a TTY. The CLI command in cli.py is the
thin adapter.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from battalion.config import DEFAULT_CONFIG_PATH, save_config
from battalion.llm.litellm_client import ModelDiversityError, _silence_litellm_output


class ProviderNotDetected(Exception):
    """litellm could not map the model string to a known provider."""


class MissingApiKey(Exception):
    """A provider requires an API key but none is present in the environment."""


class ConnectivityCheckFailed(Exception):
    """A minimal completion against a configured model did not succeed."""


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

# Providers that work without an API key (local inference, self-hosted).
_KEYLESS_PROVIDERS = {"ollama", "vllm", "lm_studio", "lmstudio", "local"}

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
) -> None:
    """Confirm a model is reachable with a working key.

    Issues a minimal one-token completion. `completion_fn` is injectable so
    tests can fake the provider round-trip; it must accept the same kwargs a
    litellm completion call does. Raises ConnectivityCheckFailed on any error.
    """
    import litellm

    _silence_litellm_output()
    fn = completion_fn or litellm.completion
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    if api_key:
        kwargs["api_key"] = api_key
    try:
        fn(**kwargs)
    except Exception as exc:  # noqa: BLE001 - any provider/auth error is a failure
        raise ConnectivityCheckFailed(
            f"Connectivity check failed for {model}: {exc}"
        ) from exc


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


def run_setup(
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    model_overrides: dict[str, str] | None = None,
    validate: bool = True,
    completion_fn: Callable[..., Any] | None = None,
    prompt: Callable[[str, str], str] | None = None,
    echo: Callable[[str], None] = print,
    existing_yaml: dict[str, Any] | None = None,
) -> dict[str, dict[str, str]]:
    """Resolve, check, validate, and persist a setup config.

    Args:
        config_path: YAML file to (read and) write.
        model_overrides: Per-node model strings from CLI flags.
        validate: Whether to run live connectivity checks before saving.
        completion_fn: Injectable completion for connectivity validation.
        prompt: Interactive prompt(message, default) -> str; None disables
                prompting (missing values fall back to defaults).
        echo: Output sink for progress/notes.
        existing_yaml: Raw existing config to preserve (defaults to the file).

    Returns:
        The models dict that was written, {"node": {"model": ...}}.

    Raises:
        ProviderNotDetected, MissingApiKey, ConnectivityCheckFailed,
        ModelDiversityError — nothing is saved until all checks pass.
    """
    path = Path(config_path)
    if existing_yaml is None and path.exists():
        import yaml

        existing_yaml = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    existing_yaml = existing_yaml or {}

    existing_models: dict[str, str] = {}
    for node, data in (existing_yaml.get("models") or {}).items():
        if isinstance(data, dict) and isinstance(data.get("model"), str):
            existing_models[node] = data["model"]

    overrides = {k: v for k, v in (model_overrides or {}).items() if v}

    # 1. Resolve a model string per node.
    resolved: dict[str, str] = {}
    for node in NODE_ORDER:
        if node in overrides:
            resolved[node] = overrides[node]
        elif node in existing_models:
            resolved[node] = existing_models[node]
        elif prompt is not None:
            resolved[node] = prompt(
                f"Model for {node} (e.g. gpt-4o-mini)", DEFAULT_MODELS[node]
            ) or DEFAULT_MODELS[node]
        else:
            resolved[node] = DEFAULT_MODELS[node]

    # 2. Normalize to explicit provider/model strings.
    normalized: dict[str, str] = {}
    for node in NODE_ORDER:
        model_name, provider = detect_provider(resolved[node])
        normalized[node] = _normalized_model_string(model_name, provider)

    # 3. BTN-14 model diversity: Driver must differ from Reviewer.
    if normalized["driver"] == normalized["reviewer"]:
        reviewer_explicit = "reviewer" in overrides or "reviewer" in existing_models
        if not reviewer_explicit:
            fallback = _pick_reviewer_fallback(normalized["driver"])
            normalized["reviewer"] = fallback
            echo(
                f"Reviewer would use the same model as Driver; set to "
                f"{fallback} to satisfy model diversity."
            )
        else:
            raise ModelDiversityError(
                "Driver and Reviewer cannot use the same model. "
                f"Both are configured with model '{normalized['driver']}'. "
                "Choose a different model for reviewer (e.g. openai/gpt-4o "
                "with an openai/gpt-4o-mini driver)."
            )

    # 4. API-key availability per provider (after diversity auto-fix may
    #    have swapped in a Reviewer from another provider).
    providers = {m.split("/", 1)[0] for m in normalized.values()}
    for provider in sorted(providers):
        if provider in _KEYLESS_PROVIDERS:
            continue
        env_vars = provider_env_vars(provider)
        if api_key_for_provider(provider) is None:
            hint = " or ".join(env_vars) if env_vars else f"{provider.upper()}_API_KEY"
            raise MissingApiKey(
                f"Provider '{provider}' requires an API key, but none of "
                f"{hint} is set in the environment. Export the key (or set "
                "it in a .env file loaded before running battalion) and rerun "
                "setup."
            )

    # 5. Live connectivity validation per provider.
    if validate:
        validated = set()
        for node in NODE_ORDER:
            provider = normalized[node].split("/", 1)[0]
            if provider in validated:
                continue
            validated.add(provider)
            echo(f"Validating connectivity: {normalized[node]} ...")
            validate_connectivity(
                normalized[node],
                api_key=api_key_for_provider(provider),
                completion_fn=completion_fn,
            )
            echo(f"  ok: {normalized[node]}")

    # 6. Persist, preserving everything else in the existing file.
    models = {node: {"model": normalized[node]} for node in NODE_ORDER}
    save_config(models, config_path=path, existing=existing_yaml)
    return models
