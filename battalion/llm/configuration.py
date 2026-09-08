"""Non-secret inference configuration and preflight identity (BTN-52)."""
from __future__ import annotations

import os
import re
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime
from ipaddress import ip_address
from typing import Any, Literal
from urllib.parse import urlsplit


class InferenceConfigurationError(ValueError):
    """Invalid or unsafe persisted inference configuration."""


class ModelDiversityError(ValueError):
    """Driver and Reviewer do not have distinct configured identities."""


KEYLESS_PROVIDERS = {
    "ollama", "ollama_chat", "vllm", "hosted_vllm", "lm_studio",
    "lmstudio", "local", "llamafile",
}


def validate_endpoint(value: str) -> None:
    """Reject credential-bearing URLs without echoing their contents."""
    try:
        if not isinstance(value, str):
            raise ValueError("endpoint must be text")
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"} and parsed.hostname
            and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment
            and "?" not in value and "#" not in value
            and "\\" not in value and not any(c.isspace() for c in value)
        )
        _ = parsed.port
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise InferenceConfigurationError(
            "endpoint_url must be an HTTP(S) base URL without user info, query, "
            "fragment, or credentials. Use api_key_env for authentication."
        )


def _loopback(endpoint: str | None) -> bool:
    if not endpoint:
        return False
    host = urlsplit(endpoint).hostname
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _check_nonsecret(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            name = str(key).lower().replace("-", "_")
            if any(part in name for part in (
                "api_key", "authorization", "password", "secret", "credential",
                "access_token", "auth_token", "bearer", "cookie",
            )) or name.endswith("_token") or name in {"headers", "extra_headers", "token", "auth"}:
                raise InferenceConfigurationError(
                    "Secrets and authentication headers cannot be stored in "
                    "extra_params; use api_key_env."
                )
            _check_nonsecret(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _check_nonsecret(child)


@dataclass
class NodeLLMConfig:
    model: str
    max_retries: int = 2
    temperature: float = 0.0
    extra_params: dict[str, Any] = field(default_factory=dict)
    endpoint_url: str | None = None
    inference_location: Literal["local", "remote", "unknown"] = "unknown"
    backend: str | None = None
    canonical_model_family: str | None = None
    api_key_env: str | None = None
    keyless: bool | None = None
    cost_classification: Literal["local", "verified-free", "paid", "unknown"] = "unknown"
    classification_source: str | None = None
    classification_observed_at: datetime | None = None
    classification_expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise InferenceConfigurationError("model must be a nonempty string")
        self.model = self.model.strip()
        if "://" in self.model or any(c.isspace() for c in self.model):
            raise InferenceConfigurationError("model must be an identifier, not a URL or whitespace-separated value")
        if self.max_retries < 0:
            raise InferenceConfigurationError("max_retries must be >= 0")
        if self.inference_location not in {"local", "remote", "unknown"}:
            raise InferenceConfigurationError("inference_location must be local, remote, or unknown")
        if self.cost_classification not in {"local", "verified-free", "paid", "unknown"}:
            raise InferenceConfigurationError("cost_classification must be local, verified-free, paid, or unknown")
        if self.keyless is not None and not isinstance(self.keyless, bool):
            raise InferenceConfigurationError("keyless must be a boolean")
        if not isinstance(self.extra_params, dict):
            raise InferenceConfigurationError("extra_params must be a mapping")
        self.extra_params = deepcopy(self.extra_params)
        # Compatibility for the previously supported LiteLLM endpoint escape hatch.
        legacy_endpoint = self.extra_params.pop("api_base", None)
        if legacy_endpoint is not None:
            if self.endpoint_url is not None and self.endpoint_url != legacy_endpoint:
                raise InferenceConfigurationError("endpoint_url conflicts with extra_params.api_base")
            self.endpoint_url = legacy_endpoint
        if self.endpoint_url is not None:
            validate_endpoint(self.endpoint_url)
            if self.inference_location == "local" and not _loopback(self.endpoint_url):
                raise InferenceConfigurationError(
                    "local inference requires a loopback endpoint; use remote or unknown for LAN/public endpoints"
                )
        _check_nonsecret(self.extra_params)
        if set(self.extra_params) & {
            "model", "messages", "temperature", "base_url", "api_base",
            "custom_llm_provider", "fallbacks", "model_list", "deployment_id",
            "context_window_fallback_dict", "api_key", "client", "stream",
            "mock_response", "azure_endpoint",
        }:
            raise InferenceConfigurationError("extra_params cannot override the configured target or request mode")
        if self.api_key_env is not None:
            if not isinstance(self.api_key_env, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.api_key_env):
                raise InferenceConfigurationError("api_key_env must name an environment variable")
            if self.keyless is True:
                raise InferenceConfigurationError("api_key_env and keyless=true are mutually exclusive")
        for name in ("backend", "canonical_model_family"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise InferenceConfigurationError(f"{name} must be a nonempty string")
                setattr(self, name, value.strip())
        if self.canonical_model_family:
            self.canonical_model_family = self.canonical_model_family.casefold()
        if self.classification_source is not None:
            if not isinstance(self.classification_source, str) or not self.classification_source.strip():
                raise InferenceConfigurationError("classification_source must be nonempty when supplied")
            self.classification_source = self.classification_source.strip()
        for name in ("classification_observed_at", "classification_expires_at"):
            value = getattr(self, name)
            if isinstance(value, str):
                try:
                    value = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    raise InferenceConfigurationError(f"{name} must be an ISO 8601 timestamp") from None
                setattr(self, name, value)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise InferenceConfigurationError(f"{name} must include a timezone")
        if self.classification_expires_at and self.classification_observed_at and self.classification_expires_at <= self.classification_observed_at:
            raise InferenceConfigurationError("classification_expires_at must be after classification_observed_at")

    @property
    def provider(self) -> str:
        return self.model.split("/", 1)[0] if "/" in self.model else ""

    @property
    def is_keyless(self) -> bool:
        if self.api_key_env:
            return False
        if self.keyless is not None:
            return self.keyless
        return _loopback(self.endpoint_url) or self.provider in KEYLESS_PROVIDERS

    def request_params(self) -> dict[str, Any]:
        """Resolve secrets only at the call boundary; never mutate configuration."""
        params = deepcopy(self.extra_params)
        if self.endpoint_url:
            params["api_base"] = self.endpoint_url
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise InferenceConfigurationError(f"Required environment variable {self.api_key_env} is not set")
            params["api_key"] = key
        elif self.is_keyless:
            # OpenAI's client requires a value even for unauthenticated servers.
            # Explicitly mask ambient provider credentials for keyless endpoints.
            params["api_key"] = "battalion-keyless"
        elif self.endpoint_url:
            raise InferenceConfigurationError(
                "An authenticated custom endpoint requires api_key_env; "
                "set keyless=true only for a server that does not require authentication"
            )
        return params

    def with_model(self, model: str) -> NodeLLMConfig:
        """A changed request cannot inherit identity or cost-admission evidence."""
        if model == self.model:
            return replace(self, model=model)
        return replace(
            self, model=model, canonical_model_family=None,
            cost_classification="unknown", classification_source=None,
            classification_observed_at=None, classification_expires_at=None,
        )

    def validation_identity(self) -> tuple[Any, ...]:
        """Effective request settings, without credentials or display metadata."""
        return (self.model, self.endpoint_url, self.temperature,
                self.extra_params, self.api_key_env, self.is_keyless)


def _requested_identity(config: NodeLLMConfig) -> str:
    return config.model.split("/", 1)[-1].casefold()


def validate_model_diversity(configs: dict[str, NodeLLMConfig]) -> None:
    """Endpoint targets require explicit concrete families; preserve legacy configs.

    Family declarations are operator assertions, not runtime resolution evidence.
    BTN-54 owns contradiction detection and resolved identity provenance.
    """
    effective = {role: configs[role] for role in ("driver", "reviewer") if role in configs}
    default = configs.get("default")
    # Keep the legacy plain-default compatibility path, but an endpoint default
    # must not bypass family checks merely because role keys were omitted.
    if default and any(
        target.endpoint_url or target.backend or target.canonical_model_family
        for target in [default, *effective.values()]
    ):
        for role in ("driver", "reviewer"):
            effective.setdefault(role, default)
    identities = []
    for role in ("driver", "reviewer"):
        config = effective.get(role)
        if config is None:
            continue
        requested = _requested_identity(config)
        if any(part in {"auto", "automatic", "smart", "fusion", "profile", "profiles"}
               for part in re.split(r"[/.:_-]", requested)):
            raise ModelDiversityError(f"{role} must request a concrete model; opaque routes cannot prove diversity")
        if (config.endpoint_url or config.backend) and not config.canonical_model_family:
            raise ModelDiversityError(f"{role} requires canonical_model_family for an endpoint target")
        identities.append(config.canonical_model_family or requested)
    if len(identities) != 2:
        return
    if identities[0] == identities[1] or _requested_identity(effective["driver"]) == _requested_identity(effective["reviewer"]):
        raise ModelDiversityError(
            "Driver and Reviewer cannot use the same model or canonical model family. "
            f"Configured families: {identities[0]}, {identities[1]}. "
            "Different endpoints, providers, and quantizations do not establish diversity."
        )
