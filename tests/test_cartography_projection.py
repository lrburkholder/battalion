"""BTN-98 canonical and generated-projection freshness tests."""

from __future__ import annotations

from datetime import datetime, timezone

from battalion.cartography import CartographyRepository, Domain, EvidenceReference, MapRevision
from battalion.identity import load_project_identity


def _revision(project_id, revision_id: str) -> MapRevision:
    observed = datetime(2026, 9, 6, tzinfo=timezone.utc)
    evidence = EvidenceReference(
        reference=f"git:{revision_id}",
        description="Observed during a refresh.",
        observed_at=observed,
    )
    return MapRevision(
        map_revision_id=revision_id,
        project_id=project_id,
        repository_revision=revision_id,
        created_at=observed,
        inspected_scope=("battalion",),
        domains=(
            Domain(
                domain_id="domain:application",
                purpose="Own application operations.",
                path_scopes=("battalion",),
                evidence=(evidence,),
            ),
        ),
    )


def test_canonical_publication_and_generated_markdown_have_independent_freshness(tmp_path):
    project = load_project_identity(tmp_path, create=True)
    repository = CartographyRepository(tmp_path)
    revision = _revision(project.project_id, "maprev:one")

    status = repository.publish_with_projection(revision)

    assert status.freshness.value == "current"
    assert repository.load() == revision
    assert "derivative projection" in (tmp_path / ".battalion/cartography/map.md").read_text()
    assert repository.queries().get("domain:application").projection_freshness == "current"


def test_projection_failure_leaves_canonical_revision_readable_and_marks_only_projection_stale(tmp_path, monkeypatch):
    project = load_project_identity(tmp_path, create=True)
    repository = CartographyRepository(tmp_path)
    revision = _revision(project.project_id, "maprev:one")

    def fail_render(_revision):
        raise OSError("simulated projection failure")

    monkeypatch.setattr("battalion.cartography.projection.render_markdown", fail_render)
    status = repository.publish_with_projection(revision)

    assert status.freshness.value == "stale"
    assert repository.load() == revision
    assert repository.queries().get("domain:application").projection_freshness == "stale"


def test_new_canonical_revision_makes_prior_markdown_projection_stale(tmp_path):
    project = load_project_identity(tmp_path, create=True)
    repository = CartographyRepository(tmp_path)
    first = _revision(project.project_id, "maprev:one")
    second = _revision(project.project_id, "maprev:two")
    repository.publish_with_projection(first)

    repository.publish(second)

    assert repository.load() == second
    assert repository.queries().get("domain:application").projection_freshness == "stale"
