"""BTN-24 acceptance tests for deterministic, role-specific Intel retrieval."""

from __future__ import annotations

from support.state import make_run_state

from battalion.context import (
    architect_context,
    driver_context,
    refactorer_context,
    reviewer_context,
)
from battalion.intel import AcceptedInstinct, IntelRepository, InstinctAudience
from battalion.intel.retrieval import InstinctRetriever
from battalion.state.models import RunState, RunStatus


def _accepted(
    instinct_id: str,
    *,
    audience: list[str],
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    tags: list[str] | None = None,
    supersedes_id: str | None = None,
) -> AcceptedInstinct:
    return AcceptedInstinct.model_validate({
        "schema_version": "1.0",
        "instinct_id": instinct_id,
        "lifecycle": "accepted",
        "recommendation": f"Guidance from {instinct_id}",
        "evidence": [{
            "run_id": "run-source",
            "node_execution_id": "node-source",
            "reference": "execution_record.node_executions[0]",
            "description": "Observed during an earlier execution.",
        }],
        "audience": audience,
        "applicability": {
            "description": "Applies to the declared task terms.",
            "include": include or [],
            "exclude": exclude or [],
        },
        "tags": tags or ["general"],
        "creation_provenance": {
            "originating_run_id": "run-source",
            "originating_node_execution_ids": ["node-source"],
            "created_at": "2026-08-13T12:00:00Z",
            "created_by": "recon",
        },
        "acceptance_provenance": {
            "accepted_at": "2026-08-13T13:00:00Z",
            "accepted_by": "operator@example.com",
        },
        "supersedes_id": supersedes_id,
    })


def _state(spec: str = "Implement deterministic write scope checks") -> RunState:
    return make_run_state(
        run_id='run-BTN-24',
        ticket_id='BTN-24',
        spec=spec,
        status=RunStatus.IN_PROGRESS,
        write_scope={'driver': ['src/'], 'refactorer': ['src/']},
        budget_limit=20,
    )


def test_retrieval_uses_active_accepted_role_and_applicability_rules(tmp_path):
    repository = IntelRepository(tmp_path / "intel")
    old = _accepted(
        "INS-OLD-WRITES", audience=["driver"], include=["write scope"]
    )
    records = [
        old,
        _accepted(
            "INS-NEW-WRITES",
            audience=["driver"],
            include=["write scope"],
            tags=["deterministic"],
            supersedes_id=old.instinct_id,
        ),
        _accepted(
            "INS-REVIEW-ONLY", audience=["reviewer"], include=["write scope"]
        ),
        _accepted(
            "INS-EXCLUDED",
            audience=["driver"],
            include=["write scope"],
            exclude=["deterministic"],
        ),
        _accepted(
            "INS-WRONG-TASK", audience=["driver"], include=["database migration"]
        ),
    ]
    for record in records:
        repository.store(record)

    result = InstinctRetriever(repository).retrieve(
        InstinctAudience.DRIVER,
        "Implement deterministic write-scope checks",
    )

    assert [item.instinct_id for item in result.selected] == ["INS-NEW-WRITES"]
    explanations = {item.instinct_id: item.reason for item in result.decisions}
    assert "INS-OLD-WRITES" not in explanations  # inactive records are never candidates
    assert "audience does not include driver" in explanations["INS-REVIEW-ONLY"]
    assert "excluded by applicability term 'deterministic'" in explanations["INS-EXCLUDED"]
    assert "no applicability include term matched" in explanations["INS-WRONG-TASK"]
    assert "included" in explanations["INS-NEW-WRITES"]


def test_ordering_prefers_applicability_then_tags_then_identifier(tmp_path):
    repository = IntelRepository(tmp_path / "intel")
    for record in [
        _accepted("INS-ZETA", audience=["architect"], include=["context"]),
        _accepted("INS-ALPHA", audience=["architect"], include=["context"]),
        _accepted(
            "INS-TAGGED",
            audience=["architect"],
            include=["context"],
            tags=["bounded", "deterministic"],
        ),
        _accepted(
            "INS-SPECIFIC",
            audience=["architect"],
            include=["context", "retrieval"],
            tags=["general"],
        ),
    ]:
        repository.store(record)

    retriever = InstinctRetriever(repository)
    first = retriever.retrieve(
        InstinctAudience.ARCHITECT,
        "Design deterministic bounded context retrieval",
    )
    second = retriever.retrieve(
        InstinctAudience.ARCHITECT,
        "Design deterministic bounded context retrieval",
    )

    expected = ["INS-SPECIFIC", "INS-TAGGED", "INS-ALPHA", "INS-ZETA"]
    assert [item.instinct_id for item in first.selected] == expected
    assert first == second


def test_each_role_receives_only_its_selected_identified_instincts(tmp_path):
    repository = IntelRepository(tmp_path / "intel")
    for record in [
        _accepted("INS-ARCH", audience=["architect"]),
        _accepted("INS-DRIVER", audience=["driver"]),
        _accepted("INS-REVIEW", audience=["reviewer"]),
        _accepted("INS-REFACTOR", audience=["refactorer"]),
    ]:
        repository.store(record)
    retriever = InstinctRetriever(repository)
    state = _state("General task with no restricted applicability")

    architect = architect_context(
        state,
        instincts=retriever.retrieve(InstinctAudience.ARCHITECT, state.spec).selected,
    )
    driver = driver_context(
        state,
        tmp_path,
        "red",
        instincts=retriever.retrieve(InstinctAudience.DRIVER, state.spec).selected,
    )
    refactorer = refactorer_context(
        state,
        tmp_path,
        instincts=retriever.retrieve(InstinctAudience.REFACTORER, state.spec).selected,
    )
    reviewer = reviewer_context(
        state,
        instincts=retriever.retrieve(InstinctAudience.REVIEWER, state.spec).selected,
    )

    assert "INS-ARCH" in architect and "INS-DRIVER" not in architect
    assert "INS-DRIVER" in driver and "INS-ARCH" not in driver
    assert "INS-REFACTOR" in refactorer and "INS-DRIVER" not in refactorer
    assert "INS-REVIEW" in reviewer
    assert all(
        item not in reviewer
        for item in ("INS-ARCH", "INS-DRIVER", "INS-REFACTOR")
    )
    assert all(
        len(value) <= 32_000 for value in (architect, driver, refactorer, reviewer)
    )


def test_instinct_entries_are_admitted_whole_within_their_context_budget(tmp_path):
    repository = IntelRepository(tmp_path / "intel")
    for index in range(10):
        record = _accepted(f"INS-BOUNDED-{index}", audience=["driver"])
        record = record.model_copy(update={"recommendation": "x" * 5_000})
        repository.store(record)
    selected = InstinctRetriever(repository).retrieve(
        InstinctAudience.DRIVER, "general work"
    ).selected

    context = driver_context(_state(), tmp_path, "red", instincts=selected)

    assert len(context) <= 32_000
    entries = context.count("### INS-BOUNDED-")
    assert 0 < entries < len(selected)
    assert context.count("Recommendation:") == entries
    assert context.count("Applicability:") == entries
    assert context.count("Tags:") == entries
