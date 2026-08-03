"""Tests for BTN-14: Model-diversity constraint between Driver and Reviewer."""

import pytest

from battalion.llm.litellm_client import build_node_configs, ModelDiversityError
from battalion.config import load_config, BattalionConfig


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
