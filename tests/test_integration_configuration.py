"""Portable, credential-free integration configuration (BTN-66)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from battalion.config import BattalionConfig, load_config
from battalion.integrations.configuration import (
    CapabilitySurface,
    IntegrationConfiguration,
    SecretReference,
    export_portable_integrations,
)


def _project_configuration() -> dict[str, object]:
    return {
        "project": {
            "integrations": {
                "github-work": {
                    "integration_id": "github-work-primary",
                    "provider": "github",
                    "transport": "http-rest",
                    "capabilities": ["work-source"],
                    "settings": {
                        "repository": "example/battalion",
                        "endpoint": "https://api.github.example",
                    },
                    "credential_references": {
                        "access_token": {"reference": "env://GITHUB_TOKEN"}
                    },
                },
                "operator-notices": {
                    "integration_id": "operator-notices-primary",
                    "provider": "discord",
                    "transport": "webhook",
                    "capabilities": ["notification", "outbound-event-sink"],
                    "settings": {"endpoint": "https://hooks.example/notification"},
                    "credential_references": {
                        "webhook_secret": {"reference": "env://DISCORD_WEBHOOK_SECRET"}
                    },
                },
            }
        }
    }


def test_project_integrations_bind_stable_provider_transport_and_capabilities():
    configuration = IntegrationConfiguration.model_validate(_project_configuration())

    github = configuration.project.integrations["github-work"]
    assert github.integration_id == "github-work-primary"
    assert github.provider == "github"
    assert github.transport == "http-rest"
    assert github.capabilities == frozenset({CapabilitySurface.WORK_SOURCE})
    assert github.credential_references["access_token"].reference == "env://GITHUB_TOKEN"


def test_load_config_keeps_legacy_configuration_and_adds_portable_integrations(tmp_path):
    path = tmp_path / "battalion.config.yaml"
    path.write_text(
        """
models:
  driver:
    model: openai/driver
  reviewer:
    model: anthropic/reviewer
budget_limit: 7
integrations:
  project:
    integrations:
      local-backlog:
        integration_id: local-backlog-v1
        provider: battalion-backlog
        transport: native-local
        capabilities: [work-source]
        settings:
          path: tickets.json
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.budget_limit == 7
    assert config.models["driver"].model == "openai/driver"
    assert config.integrations.project.integrations["local-backlog"].provider == (
        "battalion-backlog"
    )


def test_load_config_reads_shareable_sibling_integration_configuration(tmp_path):
    path = tmp_path / "battalion.config.yaml"
    path.write_text("budget_limit: 7\n", encoding="utf-8")
    (tmp_path / "battalion.integrations.yaml").write_text(
        """
project:
  integrations:
    local-backlog:
      integration_id: local-backlog-v1
      provider: battalion-backlog
      transport: native-local
      capabilities: [work-source]
      settings:
        path: tickets.json
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.budget_limit == 7
    assert config.integrations.project.integrations["local-backlog"].integration_id == (
        "local-backlog-v1"
    )


@pytest.mark.parametrize(
    "reference",
    ["GITHUB_TOKEN", "file://C:/token", "literal-secret", "vault://unapproved"],
)
def test_only_approved_secret_reference_schemes_are_accepted(reference):
    with pytest.raises(ValidationError, match="approved secret reference"):
        SecretReference(reference=reference)


def test_secret_bearing_settings_are_rejected_instead_of_exported():
    data = _project_configuration()
    data["project"]["integrations"]["github-work"]["settings"]["api_token"] = "secret"  # type: ignore[index]

    with pytest.raises(ValidationError, match="credential_references"):
        IntegrationConfiguration.model_validate(data)


def test_actor_preferences_cannot_select_a_project_forbidden_integration():
    data = _project_configuration()
    data["organization"] = {"allowed_integrations": ["github-work"]}
    data["actor_preferences"] = {
        "actor-1": {"preferred_integrations": {"notification": "operator-notices"}}
    }

    with pytest.raises(ValidationError, match="not permitted by project policy"):
        IntegrationConfiguration.model_validate(data)


def test_actor_preferences_can_only_select_a_registered_matching_capability():
    data = _project_configuration()
    data["actor_preferences"] = {
        "actor-1": {"preferred_integrations": {"notification": "github-work"}}
    }

    with pytest.raises(ValidationError, match="does not provide capability"):
        IntegrationConfiguration.model_validate(data)


def test_portable_export_retains_references_but_no_secret_material():
    data = _project_configuration()
    data["project"]["integrations"]["github-work"]["credential_references"]["access_token"] = {  # type: ignore[index]
        "reference": "env://GITHUB_TOKEN"
    }
    config = BattalionConfig(integrations=IntegrationConfiguration.model_validate(data))

    exported = export_portable_integrations(config)

    assert exported["project"]["integrations"]["github-work"]["credential_references"][
        "access_token"
    ] == {"reference": "env://GITHUB_TOKEN"}
    assert set(
        exported["project"]["integrations"]["operator-notices"][
            "credential_references"
        ]["webhook_secret"]
    ) == {"reference"}
