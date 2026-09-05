"""Deterministic contract tests for the BTN-72 GitHub Issues adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

import pytest

from battalion.application import StartWorkItemRun, start_work_item_run
from battalion.config import BattalionConfig
from battalion.integrations.configuration import IntegrationConfiguration, TransportKind
from battalion.integrations.effects import ProviderEvidence, SideEffectCoordinator
from battalion.integrations.github import (
    GitHubIssueMutationService,
    GitHubIssueWorkSource,
    github_work_source_registration,
)
from battalion.integrations.runtime import (
    IntegrationMalformedResponse,
    IntegrationPolicyDenied,
    IntegrationRuntime,
    TransportCall,
    TransportOperation,
    TransportResponse,
)
from battalion.state.models import RunStatus
from support.state import make_run_state


NOW = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
ISSUE: dict[str, Any] = {
    "id": 2048,
    "node_id": "I_kwDOExample",
    "number": 100,
    "title": "BTN-72 — GitHub Issues WorkSource adapter",
    "body": "Normalize this GitHub Issue.",
    "state": "open",
    "labels": [{"name": "priority:P1"}, {"name": "phase:implementation"}],
    "assignee": {"login": "owner"},
    "assignees": [{"login": "owner"}, {"login": "reviewer"}],
    "html_url": "https://github.example/acme/battalion/issues/100",
    "updated_at": "2026-08-27T10:00:00Z",
}


class FakeGitHubClient:
    def __init__(self, issue: Mapping[str, Any] = ISSUE) -> None:
        self.issue = issue
        self.get_calls: list[tuple[str, int]] = []
        self.comment_calls: list[tuple[str, int, str, str]] = []
        self.transition_calls: list[tuple[str, int, str, str]] = []

    def get_issue(self, repository: str, issue_number: int) -> Mapping[str, Any]:
        self.get_calls.append((repository, issue_number))
        return self.issue

    def comment(
        self, repository: str, issue_number: int, body: str, operation_id: str
    ) -> ProviderEvidence:
        self.comment_calls.append((repository, issue_number, body, operation_id))
        return ProviderEvidence(provider_reference="https://github.example/comment/1")

    def transition(
        self, repository: str, issue_number: int, status: str, operation_id: str
    ) -> ProviderEvidence:
        self.transition_calls.append((repository, issue_number, status, operation_id))
        return ProviderEvidence(provider_reference="https://github.example/issue/100")


class RecordingPolicy:
    def __init__(self, *, deny: bool = False) -> None:
        self.deny = deny
        self.calls: list[tuple[str, str]] = []

    def authorize(self, *, operation: str, external_id: str) -> None:
        self.calls.append((operation, external_id))
        if self.deny:
            raise IntegrationPolicyDenied("policy does not permit GitHub mutation")


def test_get_normalizes_a_github_issue_into_provider_neutral_work_item():
    client = FakeGitHubClient()
    source = GitHubIssueWorkSource("github-primary", "acme/battalion", client, clock=lambda: NOW)

    item = source.get("100")

    assert client.get_calls == [("acme/battalion", 100)]
    assert item.source_integration_id == "github-primary"
    assert item.external_id == "100"
    assert item.title == ISSUE["title"]
    assert item.description == ISSUE["body"]
    assert item.status == "open"
    assert item.labels == ("priority:P1", "phase:implementation")
    assert item.assignment_references == ("owner", "reviewer")
    assert item.reference_url == ISSUE["html_url"]
    assert item.source_revision == ISSUE["updated_at"]
    assert item.provenance.retrieved_at == NOW
    assert item.provenance.operation == "work.get"
    assert item.provenance.evidence == {
        "repository": "acme/battalion",
        "issue_number": "100",
        "node_id": "I_kwDOExample",
        "updated_at": "2026-08-27T10:00:00Z",
    }


def test_refresh_preserves_source_identity_and_rejects_other_integrations():
    client = FakeGitHubClient()
    source = GitHubIssueWorkSource("github-primary", "acme/battalion", client, clock=lambda: NOW)
    item = source.get("100")

    refreshed = source.refresh(item)

    assert refreshed.provenance.operation == "work.refresh"
    assert client.get_calls == [("acme/battalion", 100), ("acme/battalion", 100)]
    with pytest.raises(IntegrationMalformedResponse, match="different integration"):
        source.refresh(item.model_copy(update={"source_integration_id": "elsewhere"}))


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({**ISSUE, "number": "not-a-number"}, "positive integer"),
        ({**ISSUE, "labels": ["priority:P1"]}, "collection item"),
        ({**ISSUE, "title": ""}, "title"),
        ("not an object", "response must be an object"),
    ],
)
def test_malformed_provider_payload_never_crosses_the_work_item_boundary(payload, message):
    source = GitHubIssueWorkSource("github-primary", "acme/battalion", FakeGitHubClient(payload), clock=lambda: NOW)

    with pytest.raises(IntegrationMalformedResponse, match=message):
        source.get("100")


def test_mutations_require_policy_and_record_replay_safe_side_effect_evidence():
    client = FakeGitHubClient()
    policy = RecordingPolicy()
    service = GitHubIssueMutationService(
        integration_id="github-primary",
        integration_name="github-work",
        repository="acme/battalion",
        client=client,
        policy=policy,
    )
    state = make_run_state(ticket_id="BTN-72")
    coordinator = SideEffectCoordinator(state, clock=lambda: NOW)
    persisted: list[int] = []

    first = service.comment(
        "100", "Beginning implementation.", coordinator=coordinator,
        persist=lambda: persisted.append(1), dedupe_key="btn72-start",
    )
    replay = service.comment(
        "100", "Beginning implementation.", coordinator=coordinator,
        persist=lambda: persisted.append(1), dedupe_key="btn72-start",
    )

    assert policy.calls == [("work.comment", "100"), ("work.comment", "100")]
    assert len(client.comment_calls) == 1
    assert client.comment_calls[0][:3] == ("acme/battalion", 100, "Beginning implementation.")
    assert client.comment_calls[0][3] == first.operation_id
    assert replay.replayed is True
    assert persisted == [1, 1]
    operation = state.side_effect_ledger.operations[0]
    assert operation.operation == "work.comment"
    assert operation.attempts[0].provider_reference == "https://github.example/comment/1"
    assert operation.attempts[0].request_digest


def test_denied_mutation_never_contacts_github_or_opens_a_ledger_operation():
    client = FakeGitHubClient()
    service = GitHubIssueMutationService(
        integration_id="github-primary", integration_name="github-work",
        repository="acme/battalion", client=client, policy=RecordingPolicy(deny=True),
    )
    state = make_run_state(ticket_id="BTN-72")

    with pytest.raises(IntegrationPolicyDenied):
        service.transition(
            "100", "closed", coordinator=SideEffectCoordinator(state),
            persist=lambda: None, dedupe_key="btn72-close",
        )

    assert client.transition_calls == []
    assert state.side_effect_ledger.operations == []


def test_registered_http_adapter_uses_only_bounded_transport_and_never_exposes_mutations():
    class FakeTransport:
        def __init__(self) -> None:
            self.calls: list[tuple[TransportOperation, TransportCall]] = []

        def invoke(self, operation: TransportOperation, call: TransportCall) -> TransportResponse:
            self.calls.append((operation, call))
            return TransportResponse(ISSUE)

    transport = FakeTransport()
    bindings = []
    configuration = IntegrationConfiguration.model_validate(
        {
            "project": {"integrations": {"github-work": {
                "integration_id": "github-primary", "provider": "github",
                "transport": "http-rest", "capabilities": ["work-source"],
                "settings": {"repository": "acme/battalion"},
                "credential_references": {"access_token": {"reference": "env://GITHUB_TOKEN"}},
            }}}
        }
    )
    runtime = IntegrationRuntime(
        configuration,
        adapters=(github_work_source_registration(),),
        transports={TransportKind.HTTP_REST: lambda binding: (bindings.append(binding), transport)[1]},
    )

    port = runtime.work_source("github-work")

    assert port.get("100").external_id == "100"
    assert not hasattr(port, "comment")
    assert transport.calls == [
        (TransportOperation.HTTP_REQUEST, TransportCall({
            "method": "GET", "path": "/repos/acme/battalion/issues/100",
            "accept": "application/vnd.github+json",
        }))
    ]
    assert bindings[0].credential_references["access_token"].reference == "env://GITHUB_TOKEN"


def test_configured_github_issue_starts_a_normal_run_without_provider_leakage(tmp_path):
    class FakeTransport:
        def invoke(self, operation: TransportOperation, call: TransportCall) -> TransportResponse:
            assert operation is TransportOperation.HTTP_REQUEST
            return TransportResponse(ISSUE)

    configuration = IntegrationConfiguration.model_validate(
        {"project": {"integrations": {"github-work": {
            "integration_id": "github-primary", "provider": "github",
            "transport": "http-rest", "capabilities": ["work-source"],
            "settings": {"repository": "acme/battalion"},
        }}}}
    )
    runtime = IntegrationRuntime(
        configuration,
        adapters=(github_work_source_registration(),),
        transports={TransportKind.HTTP_REST: lambda binding: FakeTransport()},
    )

    result = start_work_item_run(
        StartWorkItemRun(
            ticket_id="BTN-72", integration_name="github-work", external_id="100",
            config=BattalionConfig(base_dir=str(tmp_path), integrations=configuration),
        ),
        integration_runtime=runtime,
        state_dir=tmp_path / "state",
        _execute=lambda **kwargs: kwargs["initial_state"].model_copy(
            update={"status": RunStatus.DONE, "phase": "done"}
        ),
    )

    assert result.state.work_item is not None
    assert result.state.work_item.external_id == "100"
    assert result.state.spec == ISSUE["body"]
