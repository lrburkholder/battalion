"""Execute every BTN-86 candidate against the same persistence control case."""

from __future__ import annotations

from pathlib import Path

from benchmarks.persistence.candidates import CANDIDATES, GraphMarkdownProjectionCandidate, PersistenceCandidate
from benchmarks.persistence.contract import (
    IntegrationSpikeTrace,
    PersistenceTrace,
    build_fixture,
    build_integration_fixture,
    fixture_with_generated_change,
    integration_fixture_after_repository_change,
    validate_trace,
)


def run_candidate(candidate: PersistenceCandidate, root: Path) -> PersistenceTrace:
    before, after = build_fixture(), fixture_with_generated_change()
    current_root, before_root, after_root = root / "current", root / "before", root / "after"
    candidate.publish(current_root, before)
    candidate.publish(before_root, before)
    candidate.publish(after_root, after)
    candidate.publish(current_root, after, fail=True)
    recovered = candidate.load(current_root)
    trace = PersistenceTrace(
        candidate=candidate.name,
        retrieval_ids=candidate.retrieve(current_root),
        neighbor_ids=candidate.traverse(current_root),
        diff=candidate.diff(before_root, after_root),
        recovered_revision=recovered.revision_id,
        generated_projection=candidate.produces_projection,
    )
    validate_trace(trace)
    return trace


def run_all(root: Path) -> tuple[PersistenceTrace, ...]:
    return tuple(run_candidate(candidate, root / candidate.name) for candidate in CANDIDATES)


def run_graph_markdown_spike(root: Path) -> IntegrationSpikeTrace:
    """Exercise the structured-canonical, human-projection option end to end."""
    candidate = GraphMarkdownProjectionCandidate()
    before, after = build_integration_fixture(), integration_fixture_after_repository_change()
    before_root, after_root, current_root = root / "before", root / "after", root / "current"
    candidate.publish(before_root, before)
    candidate.publish(after_root, after)
    candidate.publish(current_root, before)
    status_before_failure = candidate.projection_status(current_root)
    candidate.publish(current_root, after, fail_projection=True)
    status_after_failure = candidate.projection_status(current_root)
    changes = candidate.change_summary(before_root, after_root)
    return IntegrationSpikeTrace(
        before_lookup=candidate.path_lookup(before_root, "battalion/application.py"),
        after_lookup=candidate.path_lookup(after_root, "battalion/application/run.py"),
        added=changes.added,
        changed=changes.changed,
        deleted=changes.deleted,
        preserved_authored=changes.preserved_authored,
        projection_before_failure=status_before_failure,
        projection_after_failure=status_after_failure,
    )
