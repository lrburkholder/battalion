"""Load per-node LLM config from YAML + env + CLI overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from battalion.llm.litellm_client import NodeLLMConfig


DEFAULT_CONFIG_PATH = Path("battalion.config.yaml")


class BattalionConfig(BaseModel):
    """Battalion configuration loaded from YAML file."""

    models: dict[str, NodeLLMConfig] = Field(default_factory=dict)
    base_dir: str = "."
    prompts_dir: str | None = None
    budget_limit: int = 100
    manual_checkpoints: list[str] = Field(default_factory=list)
    write_scope: dict[str, list[str]] = Field(default_factory=dict)


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
    """
    # 1. Load YAML config file
    yaml_data: dict[str, Any] = {}
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if path.exists():
        import yaml
        yaml_data = yaml.safe_load(path.read_text()) or {}

    # 2. Apply environment variable overrides
    env_models = {}
    for node in ("architect", "driver", "reviewer", "refactorer"):
        model_key = f"BATTALION_MODEL_{node.upper()}"
        if model_key in os.environ:
            env_models[node] = NodeLLMConfig(model=os.environ[model_key])

    # 3. Build config with priority: YAML -> env -> CLI
    models = {}

    # Start with YAML models
    for node, model_data in yaml_data.get("models", {}).items():
        models[node] = NodeLLMConfig(**model_data)

    # Apply env overrides
    models.update(env_models)

    # Apply CLI overrides
    if cli_overrides:
        for node in ("architect", "driver", "reviewer", "refactorer"):
            key = f"model_{node}"
            if key in cli_overrides and cli_overrides[key]:
                models[node] = NodeLLMConfig(model=cli_overrides[key])

    # If no models configured at all, provide a default
    if not models:
        models["default"] = NodeLLMConfig(model="gpt-4o-mini")

    # Merge other config fields
    base_dir = cli_overrides.get("base_dir", yaml_data.get("base_dir", "."))
    prompts_dir = cli_overrides.get("prompts_dir", yaml_data.get("prompts_dir"))
    budget_limit = cli_overrides.get("budget_limit", yaml_data.get("budget_limit", 100))
    manual_checkpoints = cli_overrides.get("manual_checkpoints", yaml_data.get("manual_checkpoints", []))
    write_scope = yaml_data.get("write_scope", {
        "architect": ["plan.md"],
        "driver": ["src/"],
        "reviewer": [],
    })

    return BattalionConfig(
        models=models,
        base_dir=base_dir,
        prompts_dir=prompts_dir,
        budget_limit=budget_limit,
        manual_checkpoints=manual_checkpoints,
        write_scope=write_scope,
    )