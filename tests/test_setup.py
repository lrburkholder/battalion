"""Tests for `battalion setup` (BTN-15)."""

import pytest
import yaml
from typer.testing import CliRunner

from battalion.cli import app
from battalion.config import load_config
from battalion.llm.litellm_client import ModelDiversityError
from battalion.setup import (
    ConnectivityCheckFailed,
    MissingApiKey,
    ProviderNotDetected,
    api_key_for_provider,
    detect_provider,
    run_setup,
    save_config,
    validate_connectivity,
)


def fake_detect(model: str) -> tuple[str, str]:
    """Stand-in for litellm.get_llm_provider: split the prefix, else openai."""
    if "/" in model:
        provider, name = model.split("/", 1)
    else:
        provider, name = "openai", model
    return name, provider


def fake_key(provider: str) -> str | None:
    """Stand-in for api_key_for_provider: openai/anthropic/mistral have keys."""
    return {"openai": "sk-openai", "anthropic": "sk-anthropic", "mistral": "sk-mistral", "ollama": None}.get(provider)


@pytest.fixture(autouse=True)
def patch_provider_layer(monkeypatch):
    monkeypatch.setattr("battalion.setup.detect_provider", fake_detect)
    monkeypatch.setattr("battalion.setup.api_key_for_provider", fake_key)


def test_fresh_setup_creates_loadable_config(tmp_path):
    """AC1: no config file -> a valid battalion.config.yaml with working models."""
    path = tmp_path / "battalion.config.yaml"
    assert not path.exists()

    written = run_setup(config_path=path, validate=False)

    assert path.exists()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert set(raw["models"]) == {"architect", "driver", "reviewer", "refactorer"}
    for node in raw["models"]:
        assert raw["models"][node]["model"] == written[node]["model"]
        assert "/" in raw["models"][node]["model"]

    # The written config is valid: load_config round-trips it.
    cfg = load_config(path)
    assert set(cfg.models) == {"architect", "driver", "reviewer", "refactorer"}
    assert cfg.models["driver"].model != cfg.models["reviewer"].model


def test_disclosure_precedes_setup_prompts_and_provider_calls(tmp_path):
    from battalion.disclosure import DATA_HANDLING_URL

    notices = []
    calls = []

    def assert_disclosed():
        assert DATA_HANDLING_URL in notices[0]
        assert "live provider request" in notices[0]

    def prompt(message, default):
        assert_disclosed()
        return default

    def completion(**kwargs):
        assert_disclosed()
        calls.append(kwargs)

    run_setup(
        config_path=tmp_path / "battalion.config.yaml",
        prompt=prompt, echo=notices.append, completion_fn=completion,
    )
    assert calls
    assert all(call["messages"] == [{"role": "user", "content": "ping"}] for call in calls)


def test_existing_config_preserved_except_specified_changes(tmp_path):
    """AC4: existing keys survive; only specified node changes."""
    path = tmp_path / "battalion.config.yaml"
    existing = {
        "models": {
            "architect": {"model": "openai/gpt-4o"},
            "driver": {"model": "openai/gpt-4o-mini"},
            "reviewer": {"model": "anthropic/claude-3-5-sonnet-20241022"},
            "refactorer": {"model": "openai/gpt-4o-mini"},
        },
        "budget_limit": 7,
        "manual_checkpoints": ["reviewer"],
        "custom_field": {"nested": True},
    }
    path.write_text(yaml.safe_dump(existing), encoding="utf-8")

    written = run_setup(
        config_path=path,
        model_overrides={"architect": "mistral/mistral-medium-latest"},
        validate=False,
    )

    assert written["architect"]["model"] == "mistral/mistral-medium-latest"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    # Specified node changed.
    assert raw["models"]["architect"]["model"] == "mistral/mistral-medium-latest"
    # Unspecified nodes preserved.
    assert raw["models"]["driver"]["model"] == "openai/gpt-4o-mini"
    assert raw["models"]["reviewer"]["model"] == "anthropic/claude-3-5-sonnet-20241022"
    # Unrelated top-level keys preserved.
    assert raw["budget_limit"] == 7
    assert raw["manual_checkpoints"] == ["reviewer"]
    assert raw["custom_field"] == {"nested": True}


def test_connectivity_validated_once_per_provider(tmp_path, monkeypatch):
    """AC2: every configured provider is validated before setup completes."""
    calls = []

    def fake_validate(model, api_key=None, completion_fn=None):
        calls.append((model, api_key))

    monkeypatch.setattr("battalion.setup.validate_connectivity", fake_validate)
    path = tmp_path / "battalion.config.yaml"

    run_setup(
        config_path=path,
        model_overrides={
            "architect": "openai/gpt-4o-mini",
            "driver": "openai/gpt-4o-mini",
            "reviewer": "anthropic/claude-3-5-sonnet-20241022",
            "refactorer": "openai/gpt-4o-mini",
        },
        validate=True,
    )

    assert sorted(c[0] for c in calls) == [
        "anthropic/claude-3-5-sonnet-20241022",
        "openai/gpt-4o-mini",
    ]
    assert ("openai/gpt-4o-mini", "sk-openai") in calls
    assert ("anthropic/claude-3-5-sonnet-20241022", "sk-anthropic") in calls
    assert path.exists()


def test_connectivity_failure_aborts_before_save(tmp_path, monkeypatch):
    """AC2: a failed connectivity check stops setup and writes nothing."""
    def boom(model, api_key=None, completion_fn=None):
        raise ConnectivityCheckFailed(f"no route to {model}")

    monkeypatch.setattr("battalion.setup.validate_connectivity", boom)
    path = tmp_path / "battalion.config.yaml"

    with pytest.raises(ConnectivityCheckFailed):
        run_setup(config_path=path, validate=True)

    assert not path.exists()


def test_diversity_enforced_on_explicit_models(tmp_path):
    """AC3: Driver == Reviewer (both explicit) is rejected."""
    path = tmp_path / "battalion.config.yaml"

    with pytest.raises(ModelDiversityError):
        run_setup(
            config_path=path,
            model_overrides={
                "driver": "openai/gpt-4o-mini",
                "reviewer": "openai/gpt-4o-mini",
            },
            validate=False,
        )

    assert not path.exists()


def test_diversity_auto_fixed_when_reviewer_not_explicit(tmp_path):
    """AC3: a colliding default Reviewer is auto-corrected, not rejected."""
    path = tmp_path / "battalion.config.yaml"

    written = run_setup(
        config_path=path,
        model_overrides={"driver": "openai/gpt-4o"},
        validate=False,
    )

    assert written["reviewer"]["model"] != written["driver"]["model"]
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["models"]["reviewer"]["model"] != raw["models"]["driver"]["model"]


def test_missing_api_key_raises_before_save(tmp_path, monkeypatch):
    """AC2: a provider without any API key aborts setup."""
    def no_keys(provider):
        return None

    monkeypatch.setattr("battalion.setup.api_key_for_provider", no_keys)
    path = tmp_path / "battalion.config.yaml"

    with pytest.raises(MissingApiKey) as excinfo:
        run_setup(config_path=path, validate=False)

    assert "OPENAI_API_KEY" in str(excinfo.value)
    assert not path.exists()


def test_keyless_provider_skips_key_check(tmp_path, monkeypatch):
    """Local providers (ollama) work without an API key."""
    def ollama_detect(model):
        return model, "ollama"

    def no_keys(provider):
        return None

    monkeypatch.setattr("battalion.setup.detect_provider", ollama_detect)
    monkeypatch.setattr("battalion.setup.api_key_for_provider", no_keys)
    path = tmp_path / "battalion.config.yaml"

    written = run_setup(config_path=path, validate=False)

    assert path.exists()
    assert written["driver"]["model"].startswith("ollama/")


def test_save_config_preserves_unknown_keys(tmp_path):
    """AC4 (unit): save_config merges rather than overwrites."""
    path = tmp_path / "battalion.config.yaml"
    path.write_text(
        yaml.safe_dump({"budget_limit": 3, "custom_field": "keep"}), encoding="utf-8"
    )

    save_config(
        {"architect": {"model": "openai/gpt-4o-mini"}},
        config_path=path,
    )

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["budget_limit"] == 3
    assert raw["custom_field"] == "keep"
    assert raw["models"]["architect"]["model"] == "openai/gpt-4o-mini"


def test_detect_provider_normalizes_prefix(tmp_path, monkeypatch):
    """A bare model string is resolved to an explicit provider/model form."""
    import litellm

    monkeypatch.setattr(
        "battalion.setup.api_key_for_provider",
        lambda provider: "sk-test",
    )

    name, provider = detect_provider("gpt-4o-mini")
    assert provider == "openai"
    assert name == "gpt-4o-mini"

    # Unresolvable model -> ProviderNotDetected with guidance.
    def boom(*args, **kwargs):
        raise Exception("no provider knows this model")

    monkeypatch.setattr(litellm, "get_llm_provider", boom)
    with pytest.raises(ProviderNotDetected):
        detect_provider("totally-bogus-model")


def test_validate_connectivity_passes_key_and_model():
    """validate_connectivity forwards model + api_key to the completion fn."""
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return {"choices": [{"message": {"content": "ok"}}]}

    validate_connectivity("openai/gpt-4o-mini", api_key="sk-test", completion_fn=fake_completion)

    assert captured["model"] == "openai/gpt-4o-mini"
    assert captured["api_key"] == "sk-test"
    assert captured["max_tokens"] == 1


def test_api_key_for_provider_env_priority(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert api_key_for_provider("openai") is None
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    assert api_key_for_provider("openai") == "sk-env"


def test_setup_cli_writes_config(tmp_path, monkeypatch):
    """`battalion setup` persists a config via the real run_setup path."""
    runner = CliRunner()
    path = tmp_path / "battalion.config.yaml"

    monkeypatch.setattr("battalion.setup.detect_provider", fake_detect)
    monkeypatch.setattr("battalion.setup.api_key_for_provider", fake_key)
    monkeypatch.setattr("battalion.setup.validate_connectivity", lambda *a, **k: None)

    result = runner.invoke(
        app,
        ["setup", "--config", str(path), "--model-architect", "openai/gpt-4o"],
    )

    assert result.exit_code == 0, result.output
    assert path.exists()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["models"]["architect"]["model"] == "openai/gpt-4o"
    assert "Setup complete" in result.output


def test_setup_cli_reports_errors(tmp_path, monkeypatch):
    """Setup errors surface as a non-zero exit with a readable message."""
    runner = CliRunner()
    path = tmp_path / "battalion.config.yaml"

    def missing_key(provider):
        return None

    monkeypatch.setattr("battalion.setup.detect_provider", fake_detect)
    monkeypatch.setattr("battalion.setup.api_key_for_provider", missing_key)

    result = runner.invoke(app, ["setup", "--config", str(path)])

    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output
    assert not path.exists()


def test_setup_cli_uses_default_path(tmp_path, monkeypatch):
    """Without --config, setup targets ./battalion.config.yaml."""
    runner = CliRunner()
    monkeypatch.setattr("battalion.setup.detect_provider", fake_detect)
    monkeypatch.setattr("battalion.setup.api_key_for_provider", fake_key)
    monkeypatch.setattr("battalion.setup.validate_connectivity", lambda *a, **k: None)

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["setup", "--no-validate"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "battalion.config.yaml").exists()
