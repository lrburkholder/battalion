"""BTN-59 durable Actor identity and local bootstrap tests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from uuid import UUID

import pytest

from battalion.actors import (
    BOOTSTRAP_CAPABILITIES,
    Actor,
    ActorBootstrapConsumed,
    ActorCreationProvenance,
    ActorNotFound,
    ExternalIdentity,
    ExternalIdentityAlreadyLinked,
    ExternalIdentityNotFound,
    ActorKind,
    ActorRegistry,
    MalformedActorRegistry,
    bootstrap_local_actor,
    create_actor,
    format_actor_attribution,
    link_external_identity,
    load_actor_registry,
    rename_actor,
    resolve_external_actor,
    select_local_actor,
    unlink_external_identity,
)
from battalion.application import (
    BootstrapLocalActor,
    InspectActors,
    LinkExternalIdentity,
    ResolveExternalIdentity,
    UnlinkExternalIdentity,
    establish_local_actor,
    inspect_actors,
    link_external_identity as link_external_identity_command,
    resolve_external_identity,
    unlink_external_identity as unlink_external_identity_command,
)
from battalion.identity import load_project_identity
from battalion.state.models import HumanActionRecord, RunStatus


NOW = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
PROJECT_ID = UUID("10000000-0000-4000-8000-000000000059")
ACTOR_ID = UUID("20000000-0000-4000-8000-000000000059")
EVENT_ID = UUID("30000000-0000-4000-8000-000000000059")


def _ids():
    values = iter((ACTOR_ID, EVENT_ID))
    return lambda: next(values)


def test_first_use_persists_human_actor_and_credited_bootstrap_event(tmp_path):
    load_project_identity(
        tmp_path, create=True, uuid_factory=lambda: PROJECT_ID, now=lambda: NOW
    )

    registry = bootstrap_local_actor(
        tmp_path, "Local Operator", uuid_factory=_ids(), now=lambda: NOW
    )

    actor = registry.actors[0]
    assert actor.actor_id == ACTOR_ID
    assert actor.kind is ActorKind.HUMAN
    assert actor.created_by is None
    assert actor.provenance is ActorCreationProvenance.FIRST_PROJECT_BOOTSTRAP
    assert registry.local_actor_id == ACTOR_ID
    assert registry.bootstrap_event.actor_id == ACTOR_ID
    assert registry.bootstrap_event.granted_capabilities == BOOTSTRAP_CAPABILITIES
    assert load_actor_registry(tmp_path) == registry


def test_bootstrap_is_one_time_and_does_not_use_operating_system_identity(tmp_path):
    load_project_identity(tmp_path, create=True)
    bootstrap_local_actor(tmp_path, "Chosen Name")

    with pytest.raises(ActorBootstrapConsumed):
        bootstrap_local_actor(tmp_path, "Replacement")

    raw = (tmp_path / ".battalion" / "actors.json").read_text(encoding="utf-8")
    assert "Chosen Name" in raw
    assert "username" not in raw


def test_display_name_change_preserves_actor_id_and_creation_evidence(tmp_path):
    load_project_identity(tmp_path, create=True)
    original = bootstrap_local_actor(tmp_path, "Before")
    actor = original.actors[0]
    evidence = HumanActionRecord(
        action_id="action-before-rename",
        kind="interrupt-resolution",
        actor=actor.display_name,
        actor_id=actor.actor_id,
        occurred_at=NOW,
        target="interrupt:0",
        disposition="applied",
        detail="Approved",
        resulting_state_version="1.0",
        resulting_status=RunStatus.AWAITING_HUMAN,
        resulting_phase="awaiting_human",
    )

    renamed = rename_actor(tmp_path, actor.actor_id, "After")

    assert renamed.actors[0].actor_id == actor.actor_id
    assert renamed.actors[0].created_at == actor.created_at
    assert renamed.actors[0].display_name == "After"
    assert evidence.actor == "Before"
    assert evidence.actor_id == actor.actor_id


def test_system_actor_is_explicitly_typed_and_requires_creator():
    actor = Actor(
        actor_id=ACTOR_ID,
        kind="system",
        display_name="Local inspection",
        created_at=NOW,
        created_by=EVENT_ID,
        provenance="system-provisioning",
    )

    assert actor.kind is ActorKind.SYSTEM
    with pytest.raises(ValueError, match="creating Actor"):
        Actor.model_validate({**actor.model_dump(), "created_by": None})


def test_system_actor_persists_with_typed_attribution(tmp_path):
    load_project_identity(tmp_path, create=True)
    bootstrap = bootstrap_local_actor(tmp_path, "Administrator")
    creator_id = bootstrap.local_actor_id
    assert creator_id is not None

    updated = create_actor(
        tmp_path,
        kind=ActorKind.SYSTEM,
        display_name="Local inspection service",
        created_by=creator_id,
    )
    system = updated.actors[-1]
    evidence = HumanActionRecord(
        action_id="action-system",
        kind="correction",
        actor=system.display_name,
        actor_id=system.actor_id,
        occurred_at=NOW,
        target="driver_red",
        disposition="queued",
        detail="System-generated diagnostic context",
        resulting_state_version="1.0",
        resulting_status=RunStatus.IN_PROGRESS,
        resulting_phase="driver_red",
    )

    assert load_actor_registry(tmp_path).actors[-1].kind is ActorKind.SYSTEM
    assert evidence.actor_id == system.actor_id


def test_local_human_selection_uses_actor_id_not_display_name(tmp_path):
    load_project_identity(tmp_path, create=True)
    bootstrap = bootstrap_local_actor(tmp_path, "Same Name")
    creator_id = bootstrap.local_actor_id
    assert creator_id is not None
    registry = create_actor(
        tmp_path,
        kind=ActorKind.HUMAN,
        display_name="Same Name",
        created_by=creator_id,
    )
    selected_id = registry.actors[-1].actor_id

    selected = select_local_actor(tmp_path, selected_id)

    assert selected.local_actor_id == selected_id
    assert selected.local_actor_id != creator_id


def test_literal_legacy_actor_is_presented_without_inventing_identity():
    legacy = HumanActionRecord.model_validate({
        "action_id": "legacy-action",
        "kind": "interrupt-resolution",
        "actor": "literal-old-login",
        "occurred_at": NOW,
        "target": "legacy-pause",
        "disposition": "applied",
        "detail": "Continue",
        "resulting_state_version": "1.0",
        "resulting_status": "awaiting-human",
        "resulting_phase": "awaiting_human",
    })

    assert legacy.actor == "literal-old-login"
    assert legacy.actor_id is None
    assert format_actor_attribution(legacy.actor, legacy.actor_id) == (
        "literal-old-login (legacy attribution)"
    )


def test_malformed_registry_is_rejected_instead_of_replaced(tmp_path):
    load_project_identity(tmp_path, create=True)
    path = tmp_path / ".battalion" / "actors.json"
    path.write_text('{"schema_version":"1.0","actors":"invalid"}', encoding="utf-8")

    with pytest.raises(MalformedActorRegistry):
        load_actor_registry(tmp_path)


def test_registry_rejects_actor_without_atomic_bootstrap_evidence():
    with pytest.raises(ValueError, match="before project bootstrap"):
        ActorRegistry(project_id=PROJECT_ID, actors=(Actor(
            actor_id=ACTOR_ID,
            kind="human",
            display_name="Uncredited",
            created_at=NOW,
            created_by=None,
            provenance="first-project-bootstrap",
        ),))


def test_actor_creation_and_lookup_use_shared_application_boundary(tmp_path):
    load_project_identity(tmp_path, create=True)

    created = establish_local_actor(BootstrapLocalActor(tmp_path, "Operator"))
    inspected = inspect_actors(InspectActors(tmp_path))

    assert created.local_actor is not None
    assert inspected.local_actor == created.local_actor
    assert inspected.actors == created.actors


def test_external_identity_links_are_durable_and_scoped_to_an_integration(tmp_path):
    load_project_identity(tmp_path, create=True)
    bootstrap = bootstrap_local_actor(tmp_path, "Operator")
    actor_id = bootstrap.local_actor_id
    assert actor_id is not None

    linked = link_external_identity(
        tmp_path,
        actor_id=actor_id,
        integration_id="github-personal",
        provider="github",
        external_subject="user-42",
        metadata={"login": "octo-user", "organization": {"slug": "personal"}},
    )
    second = link_external_identity(
        tmp_path,
        actor_id=actor_id,
        integration_id="github-work",
        provider="github",
        external_subject="user-42",
        metadata={"login": "octo-user", "organization": {"slug": "work"}},
    )

    assert len(linked.external_identities) == 1
    assert len(second.external_identities) == 2
    assert resolve_external_actor(tmp_path, "github-personal", "user-42").actor_id == actor_id
    assert resolve_external_actor(tmp_path, "github-work", "user-42").actor_id == actor_id
    assert load_actor_registry(tmp_path).external_identities == second.external_identities


def test_external_identity_subject_cannot_be_linked_twice_within_one_integration(tmp_path):
    load_project_identity(tmp_path, create=True)
    actor_id = bootstrap_local_actor(tmp_path, "Operator").local_actor_id
    assert actor_id is not None
    link_external_identity(
        tmp_path,
        actor_id=actor_id,
        integration_id="discord-community-one",
        provider="discord",
        external_subject="member-9",
    )

    with pytest.raises(ExternalIdentityAlreadyLinked):
        link_external_identity(
            tmp_path,
            actor_id=actor_id,
            integration_id="discord-community-one",
            provider="discord",
            external_subject="member-9",
        )


def test_external_identity_rejects_secret_metadata_and_malformed_references(tmp_path):
    with pytest.raises(ValueError, match="secret material"):
        ExternalIdentity(
            actor_id=ACTOR_ID,
            integration_id="github-work",
            provider="github",
            external_subject="user-42",
            metadata={"profile": {"api_token": "must-not-persist"}},
        )

    load_project_identity(tmp_path, create=True)
    bootstrap = bootstrap_local_actor(tmp_path, "Operator")
    assert bootstrap.local_actor_id is not None
    unknown = UUID("40000000-0000-4000-8000-000000000063")
    with pytest.raises(ActorNotFound, match=str(unknown)):
        link_external_identity(
            tmp_path,
            actor_id=unknown,
            integration_id="github-work",
            provider="github",
            external_subject="user-42",
        )

    identity = ExternalIdentity(
        actor_id=bootstrap.local_actor_id,
        integration_id="github-work",
        provider="github",
        external_subject="user-42",
    )
    with pytest.raises(ValueError, match="only one Actor"):
        ActorRegistry(
            project_id=bootstrap.project_id,
            actors=bootstrap.actors,
            local_actor_id=bootstrap.local_actor_id,
            bootstrap_event=bootstrap.bootstrap_event,
            external_identities=(identity, identity),
        )


def test_external_identity_registry_migrates_existing_actor_file_on_next_write(tmp_path):
    load_project_identity(tmp_path, create=True)
    bootstrap = bootstrap_local_actor(tmp_path, "Operator")
    actor_id = bootstrap.local_actor_id
    assert actor_id is not None
    path = tmp_path / ".battalion" / "actors.json"
    legacy = json.loads(path.read_text(encoding="utf-8"))
    legacy.pop("external_identities")
    path.write_text(json.dumps(legacy), encoding="utf-8")

    assert load_actor_registry(tmp_path).external_identities == ()
    link_external_identity(
        tmp_path,
        actor_id=actor_id,
        integration_id="slack-team",
        provider="slack",
        external_subject="U0123",
    )

    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["external_identities"] == [
        {
            "schema_version": "1.0",
            "actor_id": str(actor_id),
            "integration_id": "slack-team",
            "provider": "slack",
            "external_subject": "U0123",
            "metadata": {},
        }
    ]


def test_external_identity_commands_use_the_shared_application_boundary(tmp_path):
    load_project_identity(tmp_path, create=True)
    actor = establish_local_actor(BootstrapLocalActor(tmp_path, "Operator")).local_actor
    assert actor is not None

    linked = link_external_identity_command(
        LinkExternalIdentity(
            tmp_path,
            actor.actor_id,
            "github-work",
            "github",
            "user-42",
            {"login": "octo-user"},
        )
    )
    resolved = resolve_external_identity(
        ResolveExternalIdentity(tmp_path, "github-work", "user-42")
    )
    unlinked = unlink_external_identity_command(
        UnlinkExternalIdentity(tmp_path, "github-work", "user-42")
    )

    assert linked.registry.external_identities[0].actor_id == actor.actor_id
    assert resolved.actor == actor
    assert resolved.identity.provider == "github"
    assert unlinked.registry.external_identities == ()
    with pytest.raises(ExternalIdentityNotFound):
        resolve_external_actor(tmp_path, "github-work", "user-42")
