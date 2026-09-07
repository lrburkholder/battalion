"""Endpoint configuration and the shared runtime request boundary (BTN-52)."""
from dataclasses import asdict
from types import SimpleNamespace

import pytest
import yaml
from support.responses import litellm_response

from battalion.config import load_config
from battalion.llm.configuration import InferenceConfigurationError, NodeLLMConfig
from battalion.llm.litellm_client import InfraFailure, call_llm


@pytest.mark.parametrize("fields", [
    pytest.param({"endpoint_url": "http://user:credential@localhost:8000/v1"}, id="userinfo"),
    pytest.param({"endpoint_url": "http://localhost/v1?token=credential"}, id="query"),
    pytest.param({"endpoint_url": "http://localhost/v1#credential"}, id="fragment"),
    pytest.param({"endpoint_url": "file:///tmp/server"}, id="scheme"),
    pytest.param({"endpoint_url": "http://localhost:wrong/v1"}, id="port"),
    pytest.param({"endpoint_url": "http://localhost:99999/v1"}, id="port-range"),
    pytest.param({"endpoint_url": "http://localhost\\@remote.test"}, id="ambiguous-host"),
    pytest.param({"api_key_env": "bearer credential"}, id="credential-value"),
    pytest.param({"api_key_env": "SERVER_TOKEN", "keyless": True}, id="conflicting-auth"),
    pytest.param({"inference_location": "on-prem"}, id="classification"),
    pytest.param({"endpoint_url": "http://192.168.1.10:8000/v1", "inference_location": "local"}, id="lan-is-not-same-host"),
    pytest.param({"extra_params": {"api_key": "credential"}}, id="inline-key"),
    pytest.param({"extra_params": {"extra_headers": {"Authorization": "credential"}}}, id="headers"),
    pytest.param({"extra_params": {"options": {"access_token": "credential"}}}, id="nested-secret"),
    pytest.param({"extra_params": {"fallbacks": ["openai/other"]}}, id="fallback"),
    pytest.param({"extra_params": {"custom_llm_provider": "other"}}, id="provider-override"),
    pytest.param({"extra_params": {"model": "other"}}, id="model-override"),
    pytest.param({"extra_params": {"mock_response": "ok"}}, id="fake-connectivity"),
    pytest.param({"endpoint_url": "http://localhost:8000", "extra_params": {"api_base": "http://localhost:9000"}}, id="endpoint-conflict"),
])
def test_invalid_target_is_rejected_without_exposing_values(fields):
    with pytest.raises(InferenceConfigurationError) as error:
        NodeLLMConfig(model="openai/test", **fields)
    assert "credential@" not in str(error.value)
    assert "token=credential" not in str(error.value)


@pytest.mark.parametrize("streaming", [False, True], ids=["completion", "stream"])
@pytest.mark.parametrize("authenticated", [False, True], ids=["keyless", "bearer-reference"])
def test_runtime_uses_endpoint_and_resolves_credentials_only_for_request(monkeypatch, streaming, authenticated):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-cloud-credential")
    monkeypatch.setenv("LOCAL_SERVER_TOKEN", "local-test-credential")
    config = NodeLLMConfig(
        model="openai/qwen3", endpoint_url="http://127.0.0.1:8000/v1",
        inference_location="local", temperature=0.4, max_retries=0,
        api_key_env="LOCAL_SERVER_TOKEN" if authenticated else None,
        extra_params={"max_tokens": 512, "timeout": 10},
    )
    captured = []

    def complete(**kwargs):
        captured.append(kwargs)
        if streaming:
            return iter([SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))])])
        return {"choices": [{"message": {"content": "ok"}}]}

    call_llm("architect", config, [{"role": "user", "content": "hello"}],
             completion_fn=complete, on_stream=(lambda event: None) if streaming else None)
    assert captured[0]["api_base"] == "http://127.0.0.1:8000/v1"
    assert captured[0]["api_key"] == ("local-test-credential" if authenticated else "battalion-keyless")
    assert captured[0]["temperature"] == 0.4
    assert captured[0]["max_tokens"] == 512
    assert "local-test-credential" not in repr(asdict(config))
    assert "ambient-cloud-credential" not in repr(asdict(config))
    assert config.extra_params == {"max_tokens": 512, "timeout": 10}


def test_endpoint_failures_remain_bounded_and_do_not_expose_provider_secrets(monkeypatch):
    monkeypatch.setenv("SERVER_TOKEN", "test-secret")
    calls = []
    config = NodeLLMConfig(model="openai/qwen3", endpoint_url="http://localhost:8000/v1",
                           api_key_env="SERVER_TOKEN", max_retries=1)

    def unavailable(**kwargs):
        calls.append(kwargs)
        raise ConnectionError("Authorization: Bearer test-secret")

    with pytest.raises(InfraFailure) as error:
        call_llm("driver", config, [], completion_fn=unavailable)
    assert error.value.attempts == 2
    assert len(calls) == 2
    assert all(call["api_base"] == config.endpoint_url for call in calls)
    assert "test-secret" not in str(error.value)
    assert "test-secret" not in str(error.value.last_error)


def test_freellmapi_capacity_exhaustion_uses_existing_infra_failure_boundary(monkeypatch):
    """A routed 429 pauses through the same bounded provider-failure path."""
    monkeypatch.setenv("FREELLMAPI_TOKEN", "test-freellmapi-bearer")
    config = NodeLLMConfig(
        model="openai/qwen3-coder",
        endpoint_url="http://127.0.0.1:3001/v1",
        backend="freellmapi",
        inference_location="remote",
        api_key_env="FREELLMAPI_TOKEN",
        max_retries=1,
    )
    attempts = []

    def exhausted(**kwargs):
        attempts.append(kwargs)
        raise RuntimeError("429 routed provider capacity exhausted: Bearer test-freellmapi-bearer")

    with pytest.raises(InfraFailure) as error:
        call_llm("driver", config, [], completion_fn=exhausted)

    assert error.value.node_name == "driver"
    assert error.value.attempts == 2
    assert len(attempts) == 2
    assert all(call["api_base"] == config.endpoint_url for call in attempts)
    assert "test-freellmapi-bearer" not in str(error.value)


def test_missing_explicit_credential_never_calls_provider(monkeypatch):
    monkeypatch.delenv("MISSING_SERVER_TOKEN", raising=False)
    config = NodeLLMConfig(model="openai/qwen3", api_key_env="MISSING_SERVER_TOKEN", max_retries=0)
    calls = []
    with pytest.raises(InfraFailure):
        call_llm("architect", config, [], completion_fn=lambda **kwargs: calls.append(kwargs))
    assert calls == []


@pytest.mark.parametrize("source", ["environment", "cli"])
def test_model_override_preserves_transport_but_clears_stale_family(tmp_path, monkeypatch, source):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"models": {"architect": {
        "model": "openai/old", "canonical_model_family": "old-family",
        "endpoint_url": "http://localhost:8000/v1", "api_key_env": "SERVER_TOKEN",
        "inference_location": "remote", "temperature": 0.7,
    }}}), encoding="utf-8")
    overrides = None
    if source == "environment":
        monkeypatch.setenv("BATTALION_MODEL_ARCHITECT", "openai/new")
    else:
        overrides = {"model_architect": "openai/new"}
    config = load_config(path, cli_overrides=overrides).models["architect"]
    assert config.model == "openai/new"
    assert config.endpoint_url == "http://localhost:8000/v1"
    assert config.api_key_env == "SERVER_TOKEN"
    assert config.inference_location == "remote"
    assert config.temperature == 0.7
    assert config.canonical_model_family is None


def test_legacy_endpoint_migrates_without_mutating_input():
    params = {"api_base": "http://localhost:8000/v1", "max_tokens": 50}
    config = NodeLLMConfig(model="openai/qwen3", extra_params=params)
    assert config.endpoint_url == params["api_base"]
    assert config.extra_params == {"max_tokens": 50}
    assert "api_base" in params
    assert config.inference_location == "unknown"


@pytest.fixture
def inference_http_server():
    """A local protocol double: no real model, provider, or credentials."""
    import json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, self.headers.get("Authorization"), body))
            response = litellm_response("ok")
            response.update(id="test-completion", object="chat.completion", created=0,
                            model=body["model"], usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
            response["choices"][0].update(index=0, finish_reason="stop")
            response["choices"][0]["message"]["role"] = "assistant"
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_setup_and_runtime_use_real_litellm_against_local_protocol_double(tmp_path, monkeypatch, inference_http_server):
    from battalion.setup import run_setup

    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-local-server")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    base, requests = inference_http_server
    targets = {}
    for role, model, route in [
        ("architect", "qwen3", "one"), ("driver", "qwen3", "one"),
        ("reviewer", "llama3", "two"), ("refactorer", "qwen3", "two"),
    ]:
        targets[role] = {
            "model": f"openai/{model}", "canonical_model_family": model,
            "endpoint_url": f"{base}/{route}/v1", "inference_location": "local",
            "extra_params": {"timeout": 5},
        }
    path = tmp_path / "config.yaml"
    run_setup(config_path=path, node_overrides=targets)
    assert [(route, body["model"]) for route, _, body in requests] == [
        ("/one/v1/chat/completions", "qwen3"),
        ("/two/v1/chat/completions", "llama3"),
        ("/two/v1/chat/completions", "qwen3"),
    ]
    assert all(auth == "Bearer battalion-keyless" for _, auth, _ in requests)
    config = load_config(path).models["reviewer"]
    response = call_llm("reviewer", config, [{"role": "user", "content": "hello"}])
    assert response.choices[0].message.content == "ok"
    assert requests[-1][0] == "/two/v1/chat/completions"
    assert requests[-1][1] == "Bearer battalion-keyless"
