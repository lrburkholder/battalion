"""Load per-node LLM config from YAML + env + CLI overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from battalion.integrations.configuration import IntegrationConfiguration
from battalion.llm.configuration import NodeLLMConfig, validate_model_diversity


DEFAULT_CONFIG_PATH = Path("battalion.config.yaml")
DEFAULT_INTEGRATIONS_PATH = Path("battalion.integrations.yaml")


class BattalionConfig(BaseModel):
    """Battalion configuration loaded from YAML file."""

    models: dict[str, NodeLLMConfig] = Field(default_factory=dict)
    base_dir: str = "."
    prompts_dir: str | None = None
    budget_limit: int = 100
    reviewer_test_timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    manual_checkpoints: list[str] = Field(default_factory=list)
    write_scope: dict[str, list[str]] = Field(default_factory=dict)
    integrations: IntegrationConfiguration = Field(default_factory=IntegrationConfiguration)

def load_config(
    config_path: str | Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> BattalionConfig:
    """Load configuration from YAML file, environment variables, and CLI overrides.

    Priority order (highest wins):
    1. CLI overrides (passed as `cli_overrides` dict)
    2. Environment variables (BATTALION_*)
    3. YAML config file
    4. Defaults

    Portable integrations may additionally live in a sibling
    ``battalion.integrations.yaml`` file.  It is deliberately separate from
    the commonly local ``battalion.config.yaml`` and is replaced only by an
    explicit ``integrations`` section in that config file.
    """
    # 1. Load YAML config file
    yaml_data: dict[str, Any] = {}
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if path.exists():
        import yaml
        yaml_data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    portable_integrations: dict[str, Any] = {}
    integrations_path = path.parent / DEFAULT_INTEGRATIONS_PATH
    if integrations_path.exists():
        import yaml
        portable_integrations = (
            yaml.safe_load(integrations_path.read_text(encoding="utf-8")) or {}
        )

    # 2. Apply environment variable overrides
    env_models = {}
    for node in ("architect", "driver", "reviewer", "refactorer", "tactician"):
        model_key = f"BATTALION_MODEL_{node.upper()}"
        if model_key in os.environ:
            env_models[node] = os.environ[model_key]

    # 3. Build config with priority: YAML -> env -> CLI
    models = {}

    # Start with YAML models
    for node, model_data in yaml_data.get("models", {}).items():
        models[node] = NodeLLMConfig(**model_data)

    # Apply env overrides
    for node, model in env_models.items():
        previous = models.get(node) or models.get("default")
        models[node] = previous.with_model(model) if previous else NodeLLMConfig(model=model)

    # Apply CLI overrides
    if cli_overrides:
        for node in ("architect", "driver", "reviewer", "refactorer"):
            key = f"model_{node}"
            if key in cli_overrides and cli_overrides[key]:
                model = cli_overrides[key]
                previous = models.get(node) or models.get("default")
                models[node] = previous.with_model(model) if previous else NodeLLMConfig(model=model)

    # If no models configured at all, provide a default
    if not models:
        models["default"] = NodeLLMConfig(model="gpt-4o-mini")
    
    validate_model_diversity(models)
    
    # Merge other config fields
    base_dir = (cli_overrides or {}).get("base_dir", yaml_data.get("base_dir", "."))
    prompts_dir = (cli_overrides or {}).get("prompts_dir", yaml_data.get("prompts_dir"))
    budget_limit = (cli_overrides or {}).get("budget_limit", yaml_data.get("budget_limit", 100))
    reviewer_test_timeout_seconds = (cli_overrides or {}).get(
        "reviewer_test_timeout_seconds",
        yaml_data.get("reviewer_test_timeout_seconds", 300.0),
    )
    manual_checkpoints = (cli_overrides or {}).get("manual_checkpoints", yaml_data.get("manual_checkpoints", []))
    write_scope = yaml_data.get("write_scope", {
        "architect": ["plan.md"],
        "driver": ["src/"],
        "reviewer": [],
    })
    integrations = yaml_data.get("integrations", portable_integrations)

    return BattalionConfig(
        models=models,
        base_dir=base_dir,
        prompts_dir=prompts_dir,
        budget_limit=budget_limit,
        reviewer_test_timeout_seconds=reviewer_test_timeout_seconds,
        manual_checkpoints=manual_checkpoints,
        write_scope=write_scope,
        integrations=integrations,
    )


def save_config(
    models: dict[str, dict[str, Any]],
    config_path: str | Path | None = None,
    existing: dict[str, Any] | None = None,
) -> Path:
    """Merge the given per-node models into a battalion.config.yaml.

    Every existing top-level key that isn't being replaced is preserved
    verbatim (BTN-15 AC: "existing config files are preserved with only
    specified changes, not overwritten entirely"). `existing` defaults to
    the current file contents, or {} if the file doesn't exist yet.

    Args:
        models: {"node": {"model": "provider/model", ...}} to write.
        config_path: YAML file to write (defaults to battalion.config.yaml).
        existing: Raw YAML to merge into (defaults to the file on disk).

    Returns:
        The Path that was written.
    """
    import yaml

    # All callers of the persistence boundary must obey the non-secret schema.
    for model_data in models.values():
        NodeLLMConfig(**model_data)
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if existing is None and path.exists():
        existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    data = {**(existing or {}), "models": models}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path
