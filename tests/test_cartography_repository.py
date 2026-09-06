"""BTN-98 canonical Cartography persistence tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID

import pytest

from battalion.cartography import (
    AuthorityClass,
    CartographyRepository,
    CartographyRepositoryPort,
    Domain,
    EvidenceReference,
    InstitutionalConstraint,
    MapRevision,
)
from battalion.actors import bootstrap_local_actor
from battalion.cartography.repository import (
    CartographyActorNotFound,
    CartographyProjectMismatch,
    MapRevisionAlreadyPublished,
    MalformedCanonicalMap,
    UnsupportedCanonicalMapSchema,
)
from battalion.identity import load_project_identity


def _revision(project_id, revision_id: str, repository_revision: str) -> MapRevision:
    observed = datetime(2026, 9, 6, tzinfo=timezone.utc)
    evidence = EvidenceReference(
        reference=f"git:{repository_revision}",
        description="Observed in the selected repository revision.",
        observed_at=observed,
    )
    return MapRevision(
        map_revision_id=revision_id,
        project_id=project_id,
        repository_revision=repository_revision,
        created_at=observed,
        inspected_scope=("battalion",),
        domains=(
            Domain(
                domain_id="domain:application",
                purpose="Own transport-neutral application operations.",
                path_scopes=("battalion/application.py",),
                evidence=(evidence,),
            ),
        ),
    )


def test_publish_replaces_only_completed_canonical_map_atomically(tmp_path, monkeypatch):
    project = load_project_identity(tmp_path, create=True)
    repository = CartographyRepository(tmp_path)
    original = _revision(project.project_id, "maprev:one", "abc123")
    replacement = _revision(project.project_id, "maprev:two", "def456")
    repository.publish(original)

    def fail_replace(source, destination):
        raise OSError("simulated publication interruption")

    monkeypatch.setattr("battalion.cartography.repository.os.replace", fail_replace)
    with pytest.raises(OSError, match="interruption"):
        repository.publish(replacement)

    assert repository.load() == original


def test_persisted_json_is_deterministic_and_validated_on_read(tmp_path):
    project = load_project_identity(tmp_path, create=True)
    repository = CartographyRepository(tmp_path)
    revision = _revision(project.project_id, "maprev:one", "abc123")

    repository.publish(revision)
    raw = json.loads(repository.path.read_text(encoding="utf-8"))

    assert raw["schema_version"] == "1.1"
    assert raw["current_revision_id"] == "maprev:one"
    assert raw["revisions"]["maprev:one"]["map_revision_id"] == "maprev:one"
    assert raw["adjacency"] == {"maprev:one": {}}
    assert repository.load() == revision

    repository.path.write_text('{"schema_version":"1.0","domains":"invalid"}', encoding="utf-8")
    with pytest.raises(MalformedCanonicalMap):
        repository.load()


def test_repository_rejects_cross_project_publication(tmp_path):
    project = load_project_identity(tmp_path, create=True)
    other = load_project_identity(tmp_path / "other", create=True)
    repository = CartographyRepository(tmp_path)

    with pytest.raises(CartographyProjectMismatch, match=str(other.project_id)):
        repository.publish(_revision(other.project_id, "maprev:other", "abc123"))

    assert project.project_id != other.project_id


def test_repository_rejects_attribution_to_an_unknown_durable_actor(tmp_path):
    project = load_project_identity(tmp_path, create=True)
    revision = _revision(project.project_id, "maprev:one", "abc123").model_copy(
        update={
            "constraints": (
                InstitutionalConstraint(
                    constraint_id="constraint:write-scope",
                    statement="Do not bypass the scoped write boundary.",
                    applies_to=("domain:application",),
                    evidence=(_revision(project.project_id, "maprev:one", "abc123").domains[0].evidence[0],),
                    authority_class=AuthorityClass.ATTRIBUTED,
                    asserted_by_actor_id=UUID("4f3a1684-263d-4301-899f-9f1770fbab26"),
                ),
            ),
        }
    )

    with pytest.raises(CartographyActorNotFound, match="unknown durable Actor"):
        CartographyRepository(tmp_path).publish(revision)


def test_repository_persists_attribution_to_a_known_durable_actor(tmp_path):
    project = load_project_identity(tmp_path, create=True)
    actor_id = bootstrap_local_actor(tmp_path, "Cartography operator").local_actor_id
    assert actor_id is not None
    base = _revision(project.project_id, "maprev:one", "abc123")
    revision = base.model_copy(
        update={
            "constraints": (
                InstitutionalConstraint(
                    constraint_id="constraint:scoped-writes",
                    statement="Do not bypass the scoped write boundary.",
                    applies_to=("domain:application",),
                    evidence=(base.domains[0].evidence[0],),
                    authority_class=AuthorityClass.ATTRIBUTED,
                    asserted_by_actor_id=actor_id,
                ),
            ),
        }
    )

    repository = CartographyRepository(tmp_path)
    repository.publish(revision)

    assert repository.load().constraints[0].asserted_by_actor_id == actor_id
    assert isinstance(repository, CartographyRepositoryPort)


def test_completed_revisions_remain_immutable_and_support_persisted_diffs(tmp_path):
    project = load_project_identity(tmp_path, create=True)
    repository = CartographyRepository(tmp_path)
    original = _revision(project.project_id, "maprev:one", "abc123")
    replacement = _revision(project.project_id, "maprev:two", "def456")
    repository.publish(original)
    repository.publish(replacement)

    assert repository.load() == replacement
    assert repository.load_revision("maprev:one") == original
    assert [item.map_revision_id for item in repository.list_revisions()] == [
        "maprev:one", "maprev:two"
    ]
    assert repository.diff_from("maprev:one").prior_map_revision_id == "maprev:one"

    with pytest.raises(MapRevisionAlreadyPublished, match="maprev:one"):
        repository.publish(_revision(project.project_id, "maprev:one", "changed"))


def test_legacy_v1_store_migrates_on_next_atomic_publication_and_newer_schema_fails(tmp_path):
    project = load_project_identity(tmp_path, create=True)
    repository = CartographyRepository(tmp_path)
    original = _revision(project.project_id, "maprev:one", "abc123")
    legacy = {
        "schema_version": "1.0",
        "revision": original.model_dump(mode="json"),
        "adjacency": {},
    }
    repository.path.parent.mkdir(parents=True)
    repository.path.write_text(json.dumps(legacy), encoding="utf-8")

    assert repository.load() == original
    repository.publish(_revision(project.project_id, "maprev:two", "def456"))
    assert json.loads(repository.path.read_text(encoding="utf-8"))["schema_version"] == "1.1"

    repository.path.write_text('{"schema_version":"99.0"}', encoding="utf-8")
    with pytest.raises(UnsupportedCanonicalMapSchema, match="99.0"):
        repository.load()
