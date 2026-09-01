"""GitHub Issues WorkSource adapter (BTN-72).

GitHub payloads and transport mechanics stay here.  The rest of Battalion sees
only :class:`~battalion.work.WorkItem` snapshots, or an application-owned
mutation service that has already received policy approval and durable
side-effect coordination.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any, Protocol
from uuid import UUID

from battalion.integrations.configuration import CapabilitySurface, TransportKind
from battalion.integrations.effects import (
    DeliveryReceipt,
    ProviderEvidence,
    SideEffectCoordinator,
    request_digest,
)
from battalion.integrations.runtime import (
    AdapterBinding,
    AdapterRegistration,
    BoundTransport,
    IntegrationError,
    IntegrationMalformedResponse,
    TransportCall,
    TransportOperation,
)
from battalion.work import WorkItem, WorkItemProvenance


_REPOSITORY = re.compile(r"^[^/\s]+/[^/\s]+$")


class GitHubIssueNotFound(IntegrationError):
    """GitHub confirmed that the requested Issue does not exist."""


class GitHubIssueConflict(IntegrationError):
    """GitHub rejected a mutation because the source revision is stale."""


class GitHubIssueMutationPolicy(Protocol):
    """Application-owned policy check for one GitHub Issue mutation."""

    def authorize(self, *, operation: str, external_id: str) -> None:
        """Allow the named mutation or raise ``IntegrationPolicyDenied``."""


class GitHubIssueClient(Protocol):
    """GitHub-specific boundary replaceable by REST, GraphQL, or an SDK."""

    def get_issue(self, repository: str, issue_number: int) -> Mapping[str, Any]:
        """Return one provider payload for a GitHub Issue."""

    def comment(
        self, repository: str, issue_number: int, body: str, operation_id: str
    ) -> ProviderEvidence:
        """Create a comment, preserving Battalion's logical operation ID."""

    def transition(
        self, repository: str, issue_number: int, status: str, operation_id: str
    ) -> ProviderEvidence:
        """Change the GitHub Issue's open/closed state."""


class GitHubHttpIssueClient:
    """GitHub REST-shaped client over the runtime's bounded HTTP mechanic.

    The enclosing transport owns endpoint selection and credential resolution;
    this class receives neither secret values nor a raw HTTP client.
    """

    def __init__(self, transport: BoundTransport) -> None:
        self._transport = transport

    def get_issue(self, repository: str, issue_number: int) -> Mapping[str, Any]:
        payload = self._request(
            "GET", f"/repos/{repository}/issues/{issue_number}"
        )
        return _mapping(payload, "GitHub Issue response")

    def comment(
        self, repository: str, issue_number: int, body: str, operation_id: str
    ) -> ProviderEvidence:
        payload = self._request(
            "POST",
            f"/repos/{repository}/issues/{issue_number}/comments",
            body={"body": body},
            idempotency_key=operation_id,
        )
        response = _mapping(payload, "GitHub comment response")
        return ProviderEvidence(
            provider_reference=_string_or_none(response.get("html_url"))
        )

    def transition(
        self, repository: str, issue_number: int, status: str, operation_id: str
    ) -> ProviderEvidence:
        payload = self._request(
            "PATCH",
            f"/repos/{repository}/issues/{issue_number}",
            body={"state": status},
            idempotency_key=operation_id,
        )
        response = _mapping(payload, "GitHub Issue update response")
        return ProviderEvidence(
            provider_reference=_string_or_none(response.get("html_url"))
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        call: dict[str, Any] = {
            "method": method,
            "path": path,
            "accept": "application/vnd.github+json",
        }
        if body is not None:
            call["body"] = dict(body)
        if idempotency_key is not None:
            # GitHub does not provide a general idempotency header for Issues.
            # The concrete transport may retain this solely as correlation
            # metadata; Battalion's durable ledger remains authoritative.
            call["idempotency_key"] = idempotency_key
        return self._transport.invoke(
            TransportOperation.HTTP_REQUEST, TransportCall(call)
        ).payload


@dataclass(frozen=True)
class GitHubIssueWorkSource:
    """Read-only normalizer for one configured GitHub repository."""

    integration_id: str
    repository: str
    client: GitHubIssueClient
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    capability: CapabilitySurface = CapabilitySurface.WORK_SOURCE

    def get(self, external_id: str) -> WorkItem:
        return self._retrieve(external_id, operation="work.get")

    def refresh(self, item: WorkItem) -> WorkItem:
        if item.source_integration_id != self.integration_id:
            raise IntegrationMalformedResponse(
                "cannot refresh a WorkItem owned by a different integration"
            )
        return self._retrieve(item.external_id, operation="work.refresh")

    def _retrieve(self, external_id: str, *, operation: str) -> WorkItem:
        issue_number = _issue_number(external_id)
        payload = _mapping(
            self.client.get_issue(self.repository, issue_number),
            "GitHub Issue response",
        )
        return _normalise_issue(
            payload,
            integration_id=self.integration_id,
            repository=self.repository,
            operation=operation,
            retrieved_at=self.clock(),
        )


class GitHubIssueMutationService:
    """Policy-mediated, replay-safe GitHub Issue mutations.

    Graph nodes never receive this object.  An application service supplies the
    mutation policy, durable coordinator, and persistence callback for each
    explicit human- or application-authorized operation.
    """

    def __init__(
        self,
        *,
        integration_id: str,
        integration_name: str,
        repository: str,
        client: GitHubIssueClient,
        policy: GitHubIssueMutationPolicy,
    ) -> None:
        self._integration_id = integration_id
        self._integration_name = integration_name
        self._repository = _repository(repository)
        self._client = client
        self._policy = policy

    def comment(
        self,
        external_id: str,
        body: str,
        *,
        coordinator: SideEffectCoordinator,
        persist: Callable[[], None],
        dedupe_key: str,
        actor_id: UUID | None = None,
    ) -> DeliveryReceipt:
        """Create one approved Issue comment with durable replay evidence."""

        issue_number = _issue_number(external_id)
        if not body.strip():
            raise ValueError("GitHub Issue comment body must not be empty")
        self._policy.authorize(operation="work.comment", external_id=str(issue_number))
        return coordinator.execute(
            capability=CapabilitySurface.WORK_SOURCE,
            integration_id=self._integration_id,
            integration_name=self._integration_name,
            provider="github",
            transport=TransportKind.HTTP_REST,
            operation="work.comment",
            actor_id=actor_id,
            dedupe_key=dedupe_key,
            request_digest_value=request_digest(
                {"external_id": str(issue_number), "body": body}
            ),
            persist=persist,
            deliver=lambda operation_id: self._client.comment(
                self._repository, issue_number, body, operation_id
            ),
        )

    def transition(
        self,
        external_id: str,
        status: str,
        *,
        coordinator: SideEffectCoordinator,
        persist: Callable[[], None],
        dedupe_key: str,
        actor_id: UUID | None = None,
    ) -> DeliveryReceipt:
        """Change an Issue between GitHub's supported open/closed states."""

        issue_number = _issue_number(external_id)
        if status not in {"open", "closed"}:
            raise ValueError("GitHub Issue status must be 'open' or 'closed'")
        self._policy.authorize(operation="work.transition", external_id=str(issue_number))
        return coordinator.execute(
            capability=CapabilitySurface.WORK_SOURCE,
            integration_id=self._integration_id,
            integration_name=self._integration_name,
            provider="github",
            transport=TransportKind.HTTP_REST,
            operation="work.transition",
            actor_id=actor_id,
            dedupe_key=dedupe_key,
            request_digest_value=request_digest(
                {"external_id": str(issue_number), "status": status}
            ),
            persist=persist,
            deliver=lambda operation_id: self._client.transition(
                self._repository, issue_number, status, operation_id
            ),
        )


def github_work_source_factory(
    binding: AdapterBinding, transport: BoundTransport
) -> GitHubIssueWorkSource:
    """Build the registered GitHub WorkSource without resolving credentials."""

    if binding.provider != "github" or binding.transport is not TransportKind.HTTP_REST:
        raise IntegrationMalformedResponse("GitHub Issues requires the github/http-rest binding")
    repository = _repository(binding.settings.get("repository"))
    return GitHubIssueWorkSource(
        integration_id=binding.integration_id,
        repository=repository,
        client=GitHubHttpIssueClient(transport),
    )


def github_work_source_registration() -> AdapterRegistration:
    """Return the explicit runtime registration for GitHub Issues intake."""

    return AdapterRegistration(
        provider="github",
        transport=TransportKind.HTTP_REST,
        capability=CapabilitySurface.WORK_SOURCE,
        required_transport_operations=frozenset({TransportOperation.HTTP_REQUEST}),
        factory=github_work_source_factory,
    )


def _normalise_issue(
    payload: Mapping[str, Any],
    *,
    integration_id: str,
    repository: str,
    operation: str,
    retrieved_at: datetime,
) -> WorkItem:
    number = _issue_number(payload.get("number"))
    title = _required_string(payload.get("title"), "GitHub Issue title")
    body = payload.get("body")
    if body is not None and not isinstance(body, str):
        raise IntegrationMalformedResponse("GitHub Issue body must be a string or null")
    status = _required_string(payload.get("state"), "GitHub Issue state")
    labels = _names(payload.get("labels"), "labels")
    assignees = _names(payload.get("assignees"), "assignees", key="login")
    assignee = payload.get("assignee")
    if assignee is not None:
        assignees = tuple(dict.fromkeys((*assignees, _required_mapping_string(assignee, "login"))))

    updated_at = _string_or_none(payload.get("updated_at"))
    node_id = _string_or_none(payload.get("node_id"))
    database_id = _identifier_or_none(payload.get("id"))
    source_revision = updated_at or node_id or database_id
    evidence = {"repository": repository, "issue_number": str(number)}
    if node_id is not None:
        evidence["node_id"] = node_id
    if updated_at is not None:
        evidence["updated_at"] = updated_at

    return WorkItem(
        source_integration_id=integration_id,
        external_id=str(number),
        title=title,
        description=body or "",
        status=status,
        labels=labels,
        assignment_references=assignees,
        reference_url=_string_or_none(payload.get("html_url")),
        source_revision=source_revision,
        provenance=WorkItemProvenance(
            retrieved_at=retrieved_at,
            operation=operation,  # type: ignore[arg-type]
            evidence=evidence,
        ),
    )


def _repository(value: object) -> str:
    if not isinstance(value, str) or not _REPOSITORY.fullmatch(value):
        raise IntegrationMalformedResponse(
            "GitHub Issue integration settings require repository as 'owner/name'"
        )
    return value


def _issue_number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise IntegrationMalformedResponse("GitHub Issue ID must be a positive integer")
    rendered = str(value)
    if not rendered.isdecimal() or int(rendered) < 1:
        raise IntegrationMalformedResponse("GitHub Issue ID must be a positive integer")
    return int(rendered)


def _mapping(value: object, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IntegrationMalformedResponse(f"{description} must be an object")
    return value


def _required_string(value: object, description: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntegrationMalformedResponse(f"{description} must be a non-empty string")
    return value


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise IntegrationMalformedResponse("GitHub Issue response contains a non-string value")
    return value


def _identifier_or_none(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise IntegrationMalformedResponse("GitHub Issue response contains an invalid ID")
    return str(value)


def _names(value: object, description: str, *, key: str = "name") -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise IntegrationMalformedResponse(f"GitHub Issue {description} must be a list")
    return tuple(_required_mapping_string(item, key) for item in value)


def _required_mapping_string(value: object, key: str) -> str:
    if not isinstance(value, Mapping):
        raise IntegrationMalformedResponse("GitHub Issue collection item must be an object")
    return _required_string(value.get(key), f"GitHub Issue collection item {key}")
