"""Tests for `battalion setup` (BTN-15)."""

import pytest
import yaml
from typer.testing import CliRunner

from battalion.cli import app
from battalion.config import load_config
from battalion.llm.litellm_client import ModelDiversityError
from battalion.llm.configuration import InferenceConfigurationError
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


def test_connectivity_validated_once_per_model(tmp_path, monkeypatch):
    """Shared providers cannot suppress checks for distinct selected models."""
    calls = []

    def fake_validate(model, api_key=None, completion_fn=None, config=None):
        calls.append((model, api_key))

    monkeypatch.setattr("battalion.setup.validate_connectivity", fake_validate)
    path = tmp_path / "battalion.config.yaml"

    run_setup(
        config_path=path,
        model_overrides={
            "architect": "openai/gpt-4o-mini",
            "driver": "openai/gpt-4o-mini",
            "reviewer": "anthropic/claude-3-5-sonnet-20241022",
            "refactorer": "openai/gpt-4o",
        },
        validate=True,
    )

    assert sorted(c[0] for c in calls) == [
        "anthropic/claude-3-5-sonnet-20241022",
        "openai/gpt-4o",
        "openai/gpt-4o-mini",
    ]
    assert ("openai/gpt-4o-mini", "sk-openai") in calls
    assert ("anthropic/claude-3-5-sonnet-20241022", "sk-anthropic") in calls
    assert path.exists()


@pytest.mark.parametrize("failed_model", [
    pytest.param("openai/gpt-4o-mini", id="first-model-unavailable"),
    pytest.param("openai/gpt-4o", id="second-model-same-provider-unavailable"),
])
def test_connectivity_failure_aborts_before_save(tmp_path, monkeypatch, failed_model):
    """AC2: a failed connectivity check stops setup and writes nothing."""
    def boom(model, api_key=None, completion_fn=None, config=None):
        if model == failed_model:
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


@pytest.mark.parametrize("model,endpoint", [
    pytest.param("ollama_chat/qwen3", "http://localhost:11434", id="ollama"),
    pytest.param("openai/qwen3", "http://127.0.0.1:1234/v1", id="openai-compatible"),
    pytest.param("lm_studio/qwen3", "http://localhost:1234/v1", id="lm-studio"),
    pytest.param("hosted_vllm/qwen3", "http://localhost:8000/v1", id="vllm"),
])
def test_local_setup_uses_real_provider_detection_and_preserves_target(tmp_path, monkeypatch, model, endpoint):
    monkeypatch.setattr("battalion.setup.detect_provider", detect_provider)
    calls = []
    path = tmp_path / "config.yaml"
    written = run_setup(
        config_path=path,
        node_overrides={"architect": {
            "model": model, "endpoint_url": endpoint, "inference_location": "local",
            "backend": "workstation", "temperature": 0.3,
            "extra_params": {"timeout": 10},
        }},
        completion_fn=lambda **kwargs: calls.append(kwargs),
    )
    assert calls[0]["model"] == model
    assert calls[0]["api_base"] == endpoint
    assert calls[0]["api_key"] == "battalion-keyless"
    assert calls[0]["timeout"] == 10
    assert written["architect"]["inference_location"] == "local"
    config = load_config(path)
    assert config.models["architect"].endpoint_url == endpoint
    assert config.models["architect"].backend == "workstation"
    assert config.models["architect"].temperature == 0.3
    # Mixed local/remote configuration still checks the remote selected models.
    assert [call["model"] for call in calls[1:]] == ["openai/gpt-4o-mini", "openai/gpt-4o"]


def test_setup_preserves_complete_targets_and_extra_roles_without_mutating_input(tmp_path):
    from copy import deepcopy
    path = tmp_path / "config.yaml"
    existing = {"budget_limit": 7, "models": {
        "architect": {"model": "openai/gpt-4o-mini", "temperature": 0.4, "max_retries": 5,
                      "extra_params": {"api_base": "http://localhost:1234/v1", "max_tokens": 256}},
        "tactician": {"model": "ollama/qwen3", "endpoint_url": "http://localhost:11434"},
    }}
    original = deepcopy(existing)
    calls = []
    written = run_setup(config_path=path, existing_yaml=existing,
                        model_overrides={"architect": "openai/qwen3"},
                        completion_fn=lambda **kwargs: calls.append(kwargs))
    assert existing == original
    architect = written["architect"]
    assert architect["endpoint_url"] == "http://localhost:1234/v1"
    assert architect["temperature"] == 0.4
    assert architect["max_retries"] == 5
    assert architect["extra_params"] == {"max_tokens": 256}
    assert written["tactician"]["endpoint_url"] == "http://localhost:11434"
    assert calls[-1]["model"] == "ollama/qwen3"
    assert yaml.safe_load(path.read_text())["budget_limit"] == 7


@pytest.mark.parametrize("difference", ["endpoint", "model", "credential", "parameters"])
def test_connectivity_checks_distinct_effective_targets(tmp_path, monkeypatch, difference):
    monkeypatch.setenv("SERVER_ONE", "one-test-value")
    monkeypatch.setenv("SERVER_TWO", "two-test-value")
    first = {"model": "openai/qwen3", "endpoint_url": "http://localhost:8000/v1",
             "api_key_env": "SERVER_ONE"}
    second = dict(first)
    if difference == "endpoint":
        second["endpoint_url"] = "http://localhost:9000/v1"
    elif difference == "model":
        second["model"] = "openai/llama3"
    elif difference == "credential":
        second["api_key_env"] = "SERVER_TWO"
    else:
        second["extra_params"] = {"api_version": "2025-01-01"}
    calls = []
    run_setup(config_path=tmp_path / "config.yaml",
              node_overrides={"architect": first, "refactorer": second},
              completion_fn=lambda **kwargs: calls.append(kwargs))
    assert len(calls) == 4
    assert calls[0]["api_base"] == first["endpoint_url"]
    assert calls[-1]["api_base"] == second["endpoint_url"]
    assert calls[0] != calls[-1]


def test_connectivity_deduplicates_identical_requests_with_different_display_metadata(tmp_path):
    target = {"model": "openai/qwen3", "endpoint_url": "http://localhost:8000/v1"}
    calls = []
    run_setup(config_path=tmp_path / "config.yaml", node_overrides={
        "architect": {**target, "backend": "architect-server"},
        "refactorer": {**target, "backend": "refactorer-server", "max_retries": 5},
    }, completion_fn=lambda **kwargs: calls.append(kwargs))
    assert len(calls) == 3


def test_unavailable_endpoint_preserves_existing_file_and_hides_provider_secret(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVER_TOKEN", "test-secret")
    path = tmp_path / "config.yaml"
    original = "budget_limit: 7\n"
    path.write_text(original, encoding="utf-8")
    notices = []

    def unavailable(**kwargs):
        raise ConnectionError("Authorization: Bearer test-secret")

    with pytest.raises(ConnectivityCheckFailed) as error:
        run_setup(config_path=path, node_overrides={"architect": {
            "endpoint_url": "http://localhost:8000/v1", "api_key_env": "SERVER_TOKEN",
        }}, completion_fn=unavailable, echo=notices.append)
    assert path.read_text(encoding="utf-8") == original
    assert "test-secret" not in str(error.value)
    assert "test-secret" not in " ".join(notices)


def test_remote_proxy_on_loopback_is_keyless_without_claiming_local_inference(tmp_path, monkeypatch):
    def unexpected_key_lookup(provider):
        pytest.fail("A keyless target must not request cloud credentials")
    monkeypatch.setattr("battalion.setup.api_key_for_provider", unexpected_key_lookup)
    targets = {role: {"model": f"openai/{model}", "endpoint_url": "http://localhost:8000/v1",
                      "inference_location": "remote", "canonical_model_family": model}
               for role, model in [("architect", "qwen3"), ("driver", "qwen3"),
                                   ("reviewer", "llama3"), ("refactorer", "qwen3")]}
    path = tmp_path / "config.yaml"
    written = run_setup(config_path=path, node_overrides=targets, completion_fn=lambda **kwargs: None)
    assert all(target["inference_location"] == "remote" for target in written.values())
    assert "battalion-keyless" not in path.read_text()


@pytest.mark.parametrize("target", [
    pytest.param({"endpoint_url": "https://inference.example/v1"}, id="remote-auth-unspecified"),
    pytest.param({"api_key_env": "UNSET_SERVER_TOKEN"}, id="missing-reference"),
    pytest.param({"endpoint_url": "http://localhost:8000/v1", "extra_params": {"api_key": "test-secret"}}, id="inline-secret"),
])
def test_invalid_auth_fails_before_network_or_save(tmp_path, monkeypatch, target):
    monkeypatch.delenv("UNSET_SERVER_TOKEN", raising=False)
    path = tmp_path / "config.yaml"
    calls = []
    with pytest.raises(InferenceConfigurationError):
        run_setup(config_path=path, node_overrides={"architect": target},
                  completion_fn=lambda **kwargs: calls.append(kwargs))
    assert calls == []
    assert not path.exists()


def test_setup_cli_configures_endpoint_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVER_TOKEN", "test-secret")
    path = tmp_path / "config.yaml"
    result = CliRunner().invoke(app, [
        "setup", "--config", str(path), "--no-validate",
        "--model-driver", "openai/qwen3", "--endpoint", "driver=http://localhost:8000/v1",
        "--canonical-model-family", "driver=qwen3", "--inference-location", "driver=local",
        "--api-key-env", "driver=SERVER_TOKEN", "--backend", "driver=workstation",
    ])
    assert result.exit_code == 0, result.output
    driver = load_config(path).models["driver"]
    assert driver.endpoint_url == "http://localhost:8000/v1"
    assert driver.canonical_model_family == "qwen3"
    assert driver.api_key_env == "SERVER_TOKEN"
    assert "test-secret" not in path.read_text()
    assert "test-secret" not in result.output


def test_guided_setup_collects_endpoint_and_family(tmp_path):
    def prompt(message, default):
        if message.startswith("Endpoint base URL for driver"):
            return "http://localhost:8000/v1"
        if message.startswith("Inference location for driver"):
            return "local"
        if message.startswith("Concrete canonical model family for driver"):
            return "gpt-4o-mini"
        return default
    written = run_setup(config_path=tmp_path / "config.yaml", prompt=prompt, validate=False)
    assert written["driver"]["endpoint_url"] == "http://localhost:8000/v1"
    assert written["driver"]["inference_location"] == "local"
    assert written["driver"]["canonical_model_family"] == "gpt-4o-mini"


def test_existing_default_endpoint_is_inherited_without_remote_expansion(tmp_path, monkeypatch):
    monkeypatch.setattr("battalion.setup.api_key_for_provider", lambda provider: pytest.fail("unexpected cloud credential lookup"))
    default = {"model": "openai/qwen3", "endpoint_url": "http://localhost:8000/v1",
               "canonical_model_family": "qwen3", "inference_location": "local"}
    reviewer = {**default, "model": "openai/llama3", "canonical_model_family": "llama3"}
    calls = []
    written = run_setup(config_path=tmp_path / "config.yaml",
                        existing_yaml={"models": {"default": default, "reviewer": reviewer}},
                        completion_fn=lambda **kwargs: calls.append(kwargs))
    assert all(data["endpoint_url"] == default["endpoint_url"] for data in written.values())
    assert [call["model"] for call in calls] == ["openai/qwen3", "openai/llama3"]


@pytest.mark.parametrize("text", ["models: [", "[]", "models: []"], ids=["invalid-yaml", "root-sequence", "models-sequence"])
def test_setup_cli_reports_malformed_configuration_without_replacing_it(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    result = CliRunner().invoke(app, ["setup", "--config", str(path), "--no-validate"])
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert path.read_text(encoding="utf-8") == text
