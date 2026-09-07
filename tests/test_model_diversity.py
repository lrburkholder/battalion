"""Tests for BTN-14: Model-diversity constraint between Driver and Reviewer."""

import pytest

from battalion.llm.litellm_client import build_node_configs, ModelDiversityError
from battalion.config import load_config, BattalionConfig


@pytest.mark.parametrize("driver,reviewer", [
    pytest.param(
        {"model": "openai/qwen3-q4", "endpoint_url": "http://localhost:8000/v1", "canonical_model_family": "Qwen3"},
        {"model": "ollama/qwen3-q8", "endpoint_url": "http://localhost:11434", "canonical_model_family": "qwen3"},
        id="same-family-different-providers-endpoints-quantizations"),
    pytest.param({"model": "openai/gpt-4o"}, {"model": "gpt-4o"}, id="prefix-alias"),
    pytest.param({"model": "openai/qwen3", "endpoint_url": "http://localhost:8000/v1"},
                 {"model": "openai/gpt-4o"}, id="endpoint-family-missing"),
    pytest.param({"model": "openai/auto", "canonical_model_family": "qwen3"},
                 {"model": "openai/gpt-4o"}, id="opaque-route-with-declared-family"),
    pytest.param({"model": "openai/profiles/coding", "canonical_model_family": "qwen3"},
                 {"model": "openai/gpt-4o"}, id="opaque-profile"),
    pytest.param({"model": "openai/qwen3", "canonical_model_family": "family-one"},
                 {"model": "ollama/qwen3", "canonical_model_family": "family-two"}, id="same-request-conflicting-family"),
])
@pytest.mark.parametrize("boundary", ["build", "load", "setup"])
def test_canonical_diversity_rejected_at_configuration_boundaries(tmp_path, monkeypatch, driver, reviewer, boundary):
    import yaml
    from battalion.config import load_config
    from battalion.setup import run_setup

    models = {"driver": driver, "reviewer": reviewer}
    path = tmp_path / "config.yaml"
    if boundary == "load":
        path.write_text(yaml.safe_dump({"models": models}), encoding="utf-8")
    if boundary == "setup":
        monkeypatch.setattr("battalion.setup.detect_provider", lambda model: (
            tuple(reversed(model.split("/", 1))) if "/" in model else (model, "openai")
        ))
    with pytest.raises(ModelDiversityError):
        if boundary == "build":
            build_node_configs(models)
        elif boundary == "load":
            load_config(path)
        else:
            run_setup(config_path=path, existing_yaml={"models": models}, validate=False)
    if boundary == "setup":
        assert not path.exists()


def test_distinct_endpoint_families_are_admitted():
    configs = build_node_configs({
        "driver": {"model": "openai/qwen3", "endpoint_url": "http://localhost:8000/v1", "canonical_model_family": "qwen3"},
        "reviewer": {"model": "openai/llama3", "endpoint_url": "http://localhost:8000/v1", "canonical_model_family": "llama3"},
    })
    assert configs["driver"].canonical_model_family != configs["reviewer"].canonical_model_family


@pytest.mark.parametrize("boundary", ["build", "load"])
def test_endpoint_default_cannot_bypass_diversity(tmp_path, boundary):
    import yaml
    models = {"default": {"model": "openai/qwen3", "endpoint_url": "http://localhost:8000/v1",
                           "canonical_model_family": "qwen3"}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"models": models}), encoding="utf-8")
    with pytest.raises(ModelDiversityError, match="same model"):
        if boundary == "build":
            build_node_configs(models)
        else:
            load_config(path)


class TestModelDiversity:
    """Test that Driver and Reviewer cannot use the same model."""

    def test_build_node_configs_same_model_raises_error(self):
        """build_node_configs raises ModelDiversityError when driver and reviewer use same model."""
        raw_config = {
            "driver": {"model": "gpt-4o-mini"},
            "reviewer": {"model": "gpt-4o-mini"},
        }
        
        with pytest.raises(ModelDiversityError) as exc_info:
            build_node_configs(raw_config)
        
        assert "Driver and Reviewer cannot use the same model" in str(exc_info.value)
        assert "gpt-4o-mini" in str(exc_info.value)

    def test_build_node_configs_different_models_succeeds(self):
        """build_node_configs succeeds when driver and reviewer use different models."""
        raw_config = {
            "driver": {"model": "gpt-4o-mini"},
            "reviewer": {"model": "gpt-4o"},
        }
        
        # Should not raise
        configs = build_node_configs(raw_config)
        assert "driver" in configs
        assert "reviewer" in configs
        assert configs["driver"].model == "gpt-4o-mini"
        assert configs["reviewer"].model == "gpt-4o"

    def test_build_node_configs_default_model_shared_succeeds(self):
        """When both driver and reviewer use default model (not explicit), should succeed.
        
        This is allowed because neither driver nor reviewer is explicitly configured.
        """
        raw_config = {
            "default": {"model": "gpt-4o-mini"},
        }
        
        # Should not raise - neither driver nor reviewer explicitly configured
        configs = build_node_configs(raw_config)
        assert "default" in configs

    def test_build_node_configs_only_driver_configured_succeeds(self):
        """When only driver is configured (no reviewer), should succeed."""
        raw_config = {
            "driver": {"model": "gpt-4o-mini"},
        }
        
        # Should not raise - no reviewer to conflict
        configs = build_node_configs(raw_config)
        assert configs["driver"].model == "gpt-4o-mini"

    def test_build_node_configs_only_reviewer_configured_succeeds(self):
        """When only reviewer is configured (no driver), should succeed."""
        raw_config = {
            "reviewer": {"model": "gpt-4o"},
        }
        
        # Should not raise - no driver to conflict
        configs = build_node_configs(raw_config)
        assert configs["reviewer"].model == "gpt-4o"

    def test_build_node_configs_architect_and_refactorer_same_ok(self):
        """Architect and Refactorer can use the same model - only Driver/Reviewer constrained."""
        raw_config = {
            "architect": {"model": "gpt-4o-mini"},
            "refactorer": {"model": "gpt-4o-mini"},
        }
        
        # Should not raise - constraint is only for Driver/Reviewer
        configs = build_node_configs(raw_config)
        assert configs["architect"].model == "gpt-4o-mini"
        assert configs["refactorer"].model == "gpt-4o-mini"


class TestConfigModelDiversity:
    """Test model diversity enforcement in the full config loading."""

    def test_load_config_same_model_raises_error(self, tmp_path, monkeypatch):
        """load_config raises ModelDiversityError when driver and reviewer share model."""
        config_path = tmp_path / "battalion.config.yaml"
        config_path.write_text("""
models:
  driver:
    model: gpt-4o-mini
  reviewer:
    model: gpt-4o-mini
""")
        
        with pytest.raises(ModelDiversityError) as exc_info:
            load_config(str(config_path))
        
        assert "Driver and Reviewer cannot use the same model" in str(exc_info.value)

    def test_load_config_different_models_succeeds(self, tmp_path):
        """load_config succeeds when driver and reviewer have different models."""
        config_path = tmp_path / "battalion.config.yaml"
        config_path.write_text("""
models:
  driver:
    model: gpt-4o-mini
  reviewer:
    model: gpt-4o
""")
        
        # Should not raise
        cfg = load_config(str(config_path))
        assert isinstance(cfg, BattalionConfig)
        assert cfg.models["driver"].model == "gpt-4o-mini"
        assert cfg.models["reviewer"].model == "gpt-4o"

    def test_load_config_default_model_succeeds(self, tmp_path):
        """load_config succeeds when both driver and reviewer default to same model.
        
        This is allowed because neither driver nor reviewer is explicitly configured.
        """
        config_path = tmp_path / "battalion.config.yaml"
        config_path.write_text("""
models:
  default:
    model: gpt-4o-mini
""")
        
        # Should not raise - neither driver nor reviewer explicitly configured
        cfg = load_config(str(config_path))
        assert isinstance(cfg, BattalionConfig)

    def test_load_config_with_cli_override_same_model_raises_error(self, tmp_path):
        """load_config raises error when CLI overrides make driver and reviewer same."""
        config_path = tmp_path / "battalion.config.yaml"
        config_path.write_text("""
models:
  driver:
    model: gpt-4o-mini
  reviewer:
    model: gpt-4o
""")
        
        # Override reviewer to match driver
        with pytest.raises(ModelDiversityError):
            load_config(str(config_path), {"model_reviewer": "gpt-4o-mini"})
