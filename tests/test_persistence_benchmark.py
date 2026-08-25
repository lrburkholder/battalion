"""BTN-86 persistence spike control-case tests."""

from __future__ import annotations

from benchmarks.persistence.candidates import CANDIDATES, integration_templates
from benchmarks.persistence.contract import build_fixture
from benchmarks.persistence.runner import run_all, run_candidate, run_graph_markdown_spike


def test_fixture_is_deterministic_and_preserves_authority_categories():
    first = build_fixture()
    assert first == build_fixture()
    constraint = next(record for record in first.records if record.record_id.startswith("constraint:"))
    assert constraint.authored is True
    assert constraint.provenance == "governing_reference:ADR-0022"


def test_every_candidate_passes_the_same_retrieval_diff_and_recovery_contract(tmp_path):
    traces = run_all(tmp_path)
    assert [trace.candidate for trace in traces] == [candidate.name for candidate in CANDIDATES]
    assert all(trace.recovered_revision == "map-0001" for trace in traces)


def test_candidate_queries_do_not_depend_on_the_generic_load_projection(tmp_path, monkeypatch):
    """The benchmark exercises each physical representation's query boundary."""
    for candidate in CANDIDATES:
        root = tmp_path / candidate.name
        candidate.publish(root, build_fixture())
        monkeypatch.setattr(candidate, "load", lambda _: (_ for _ in ()).throw(AssertionError("generic load used")))
        assert candidate.retrieve(root) == ("domain:application", "symbol:application.run")
        assert candidate.traverse(root) == ("symbol:application.run", "resource:run-state")


def test_hybrid_and_markdown_candidates_declare_generated_projections(tmp_path):
    traces = {candidate.name: run_candidate(candidate, tmp_path / candidate.name) for candidate in CANDIDATES}
    assert traces["markdown-charters"].generated_projection is True
    assert traces["hybrid-sqlite-markdown"].generated_projection is True
    assert traces["sqlite"].generated_projection is False


def test_integration_measurement_template_prevents_raw_store_leakage():
    templates = integration_templates()
    assert {item.candidate for item in templates} == {candidate.name for candidate in CANDIDATES}
    assert all(not item.raw_store_access_exposed for item in templates)
    assert all(item.production_interfaces_changed == () for item in templates)


def test_graph_canonical_markdown_projection_spike_covers_rename_delete_and_visible_projection_failure(tmp_path):
    trace = run_graph_markdown_spike(tmp_path)

    assert trace.before_lookup == ("domain:application", "path:application", "symbol:application.run")
    assert trace.after_lookup == ("path:application", "symbol:application.run")
    assert trace.added == ("resource:durable-observation",)
    assert trace.changed == ("path:application", "symbol:application.run")
    assert trace.deleted == ("resource:legacy-event-log",)
    assert trace.preserved_authored == ("constraint:application-boundary",)
    assert trace.projection_before_failure == "ready"
    assert trace.projection_after_failure == "stale"

    projection = (tmp_path / "before" / "charter-projection.md").read_text(encoding="utf-8")
    assert "Generated projection" in projection
    assert "## Domains" in projection
    assert "## Resources" in projection
    assert "## Path bindings" in projection
    assert "battalion/application.py" in projection
    assert "resource:legacy-event-log" in projection
    assert "## Relationships" in projection
    assert "<!-- record:" not in projection
