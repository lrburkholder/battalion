"""History search, disposable storage, and descriptive aggregation contracts."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3

import pytest
from typer.testing import CliRunner

from battalion.application import ApplicationError, query_history
from battalion.cli import app
from battalion.history import HistoryQuery, IDENTITY_FIELDS, aggregate_attempts, project_run
from battalion.history_store import HistoryStore, ProjectionError
from battalion.identity import load_project_identity
from battalion.intel import (
    AcceptedInstinct, AcceptanceProvenance, InstinctApplicability,
    InstinctCreationProvenance, InstinctEvidenceReference, IntelRepository,
)
from battalion.state.models import (
    ArtifactProvenance, EvidenceReference, ExecutionRecord, LLMCallCost,
    OperatorSummary, PromptProvenance, ReviewResult,
)
from support.execution import FIXTURE_TIME, make_interrupt, make_node_execution
from support.state import make_run_state, persisted_checkpoint
from battalion.work import WorkItem, WorkItemProvenance


@pytest.fixture
def history_project(tmp_path):
    load_project_identity(tmp_path, create=True)
    attempt = make_node_execution(
        role="reviewer", phase="reviewer_green", execution_id="review-1",
        ended_at=FIXTURE_TIME + timedelta(seconds=4),
        review_result=ReviewResult(checkpoint="green-check", verdict="accepted", cause="Parser regression"),
        prompt_provenance=PromptProvenance(
            template_identity="reviewer", template_path="reviewer.md", contract_version="1.0",
            template_hash="b" * 64, battalion_revision="c" * 40,
            model_configuration_identity="d" * 64,
        ),
        artifact_provenance=[ArtifactProvenance(
            path="report.txt", sha256="a" * 64, originating_run_id="run-history",
            originating_node_execution_id="review-1",
        )],
        input_references=[EvidenceReference(kind="artifact", reference="src/parser.py")],
        operator_summary=OperatorSummary(
            what_i_did="Verified Unicode Straße parser", what_should_happen_next="Refactor",
            verification_performed=["Parser passed"], artifact_paths=["report.txt"],
            last_role="reviewer", last_node="reviewer_green", last_phase="green",
        ),
        llm_calls=[LLMCallCost(
            call_id="call-1", model="compatibility", requested_model="requested",
            response_model="response", backend="litellm", endpoint_url="https://example.test/v1",
            inference_location="remote", routed_provider="provider", routed_model="routed",
            identity_contradiction="distinct identity disproved", input_tokens=10, output_tokens=3,
            cost="0.10", cost_currency="USD", cost_source="provider-reported",
        )],
    )
    state = make_run_state(
        run_id="run-history", ticket_id="BTN-44", spec="Search history",
        execution_record=ExecutionRecord(node_executions=[attempt]),
        interrupt_log=[make_interrupt("manual-checkpoint", node_execution_id="review-1")],
    )
    path = persisted_checkpoint(tmp_path / ".battalion/state/run-history.json", state)
    return tmp_path, path, state


@pytest.mark.parametrize("filters,text", [
    pytest.param({"ticket_id": "BTN-44", "role": "reviewer"}, "", id="ticket-role"),
    pytest.param({"phase": "reviewer_green", "outcome": "succeeded"}, "", id="phase-outcome"),
    pytest.param({"artifact": "report.txt", "reference": "src/parser.py"}, "", id="artifacts"),
    pytest.param({"interrupt": "manual-checkpoint", "review": "accepted"}, "", id="interrupt-review"),
    pytest.param({"checkpoint": "green-check", "review_cause": "Parser regression"}, "", id="checkpoint-cause"),
    pytest.param({"artifact_digest": "a" * 64}, "", id="artifact-digest"),
    pytest.param({}, "STRASSE", id="unicode-summary"),
    pytest.param({}, "Parser passed", id="verification-summary"),
])
def test_search_preserves_evidence_links(history_project, filters, text):
    root, path, _ = history_project
    result = query_history(root, HistoryQuery(text, filters))
    assert result["total"] == 1
    match = result["results"][0]
    assert (match["run_id"], match["execution_id"], match["source_path"]) == (
        "run-history", "review-1", str(path),
    )
    assert result["limitations"] == []


@pytest.mark.parametrize("dimension", IDENTITY_FIELDS)
def test_identity_dimensions_are_independent(history_project, dimension):
    root, _, state = history_project
    value = getattr(state.execution_record.node_executions[0].llm_calls[0], dimension)
    result = query_history(root, HistoryQuery(filters={dimension: value}), dimension=dimension)
    assert result["groups"][0]["identity_values"] == [value]
    assert result["groups"][0]["attempts"] == 1
    if dimension != "model":
        assert query_history(root, HistoryQuery(filters={dimension: "compatibility"}))["total"] == 0


def test_projection_refresh_delete_and_version_rebuild_preserve_canonical(history_project):
    root, path, state = history_project
    original = path.read_bytes()
    result = query_history(root)
    projection = root / ".battalion/projections/history.sqlite"
    receipt = projection.with_suffix(".sha256")
    first_mtime = projection.stat().st_mtime_ns
    assert query_history(root) == result
    assert projection.stat().st_mtime_ns == first_mtime
    # A recognized old writer's output upgrades by reconstruction, not SQL migration.
    with sqlite3.connect(projection) as connection:
        connection.execute("UPDATE metadata SET value='1' WHERE key='version'")
        connection.execute("DROP TABLE costs")
        connection.execute("DROP TABLE evidence")
        connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, payload TEXT NOT NULL, text TEXT NOT NULL)")
    connection.close()
    receipt.write_text(sha256(projection.read_bytes()).hexdigest(), encoding="ascii")
    assert query_history(root) == result
    with sqlite3.connect(projection) as connection:
        assert connection.execute("SELECT value FROM metadata WHERE key='version'").fetchone() == ("2",)
        assert connection.execute("SELECT count(*) FROM costs").fetchone() == (1,)
    connection.close()
    projection.unlink()
    assert query_history(root) == result
    assert path.read_bytes() == original
    persisted_checkpoint(path, state.model_copy(update={"ticket_id": "BTN-new"}))
    assert query_history(root, HistoryQuery(filters={"ticket_id": "BTN-new"}))["total"] == 2
    assert query_history(root, HistoryQuery(filters={"ticket_id": "BTN-44"}))["total"] == 0


@pytest.mark.parametrize("damage", ["external-edit", "corruption", "missing-receipt"])
def test_unrecognized_projection_requires_explicit_replacement(history_project, damage):
    root, path, _ = history_project
    original = path.read_bytes()
    result = query_history(root)
    projection = root / ".battalion/projections/history.sqlite"
    if damage == "external-edit":
        with sqlite3.connect(projection) as connection:
            connection.execute("UPDATE evidence SET text='external edit'")
        connection.close()
    elif damage == "corruption":
        projection.write_bytes(b"corrupt sqlite")
    else:
        projection.with_suffix(".sha256").unlink()
    before = projection.read_bytes()
    with pytest.raises(ApplicationError, match="externally modified"):
        query_history(root)
    assert projection.read_bytes() == before
    assert query_history(root, rebuild=True) == result
    assert path.read_bytes() == original


def test_analytics_counts_attempts_once_and_separates_cost_evidence(history_project):
    root, path, state = history_project
    attempt = state.execution_record.node_executions[0]
    for index, (currency, source, model) in enumerate([
        ("EUR", "provider-reported", "requested"),
        ("USD", "estimated", "another"),
    ]):
        attempt.llm_calls.append(LLMCallCost(
            call_id=f"extra-{index}", model="legacy", requested_model=model,
            input_tokens=2, output_tokens=1, cost="0.20",
            cost_currency=currency, cost_source=source,
        ))
    attempt.llm_calls.append(LLMCallCost(
        call_id="unknown-cost", model="legacy", input_tokens=1, output_tokens=1,
    ))
    state.execution_record.node_executions.append(make_node_execution(
        role="reviewer", phase="reviewer_green", execution_id="legacy-attempt",
        outcome="in-progress", ended_at=None,
    ))
    persisted_checkpoint(path, state)
    result = query_history(root, HistoryQuery(limit=1), dimension="requested_model")
    assert len(result["groups"]) == 2  # Aggregates ignore pagination.
    mixed = next(group for group in result["groups"] if group["identity_status"] == "mixed")
    unknown = next(group for group in result["groups"] if group["identity_status"] == "unknown")
    assert mixed["identity_values"] == [None, "another", "requested"]
    assert (mixed["attempts"], mixed["observed_calls"], mixed["duration_seconds"]) == (1, 4, 4)
    assert (mixed["input_tokens"], mixed["output_tokens"], mixed["unknown_cost_calls"]) == (15, 6, 1)
    assert mixed["costs"] == [
        {"currency": "EUR", "source": "provider-reported", "total": "0.20", "observations": 1},
        {"currency": "USD", "source": "estimated", "total": "0.20", "observations": 1},
        {"currency": "USD", "source": "provider-reported", "total": "0.10", "observations": 1},
    ]
    assert unknown["input_tokens"] is None
    assert unknown["duration_seconds"] is None
    assert unknown["unknown_duration_attempts"] == 1
    assert unknown["attempts_without_call_evidence"] == 1
    assert unknown["evidence"][0]["ticket"] is None
    assert "confound" in result["notice"]
    for currency, source, amount in [("EUR", "provider-reported", "0.20"),
                                     ("USD", "estimated", "0.20"),
                                     ("USD", "provider-reported", "0.10")]:
        query = HistoryQuery(cost_currency=currency, cost_source=source,
                             cost_min=Decimal(amount), cost_max=Decimal(amount))
        assert query_history(root, query)["total"] == 1


def test_historical_model_does_not_become_requested_identity():
    state = make_run_state(execution_record=ExecutionRecord(node_executions=[
        make_node_execution(role="driver", phase="driver_red", model_identity="legacy/model"),
    ]))
    rows = project_run(state, "canonical.json")
    assert rows[1]["model"] == ["legacy/model"]
    for dimension in IDENTITY_FIELDS[1:]:
        assert rows[1][dimension] == [None]
    assert aggregate_attempts(rows, "requested_model")["groups"][0]["identity_status"] == "unknown"


def test_malformed_source_and_literal_search_are_visible(history_project):
    root, _, _ = history_project
    (root / ".battalion/state/bad.json").write_text("broken", encoding="utf-8")
    result = query_history(root, HistoryQuery("%' OR 1=1 --"))
    assert result["total"] == 0
    assert result["limitations"][0]["run_id"] == "bad"
    assert result["limitations"][0]["availability"] == "malformed"


def test_literal_text_preserves_quotes_and_newlines(history_project):
    root, path, state = history_project
    state.execution_record.node_executions[0].operator_summary.what_i_did = 'Read "parser"\nVerified output'
    persisted_checkpoint(path, state)
    assert query_history(root, HistoryQuery('"parser"\nVerified'))["total"] == 1


def test_cli_search_analytics_and_invalid_filter(history_project):
    root, _, _ = history_project
    runner = CliRunner()
    result = runner.invoke(app, ["history", "--project", str(root), "--filter", "role=reviewer"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["total"] == 1
    result = runner.invoke(app, ["history", "--project", str(root), "--analytics", "response_model"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["groups"][0]["identity_values"] == ["response"]
    result = runner.invoke(app, ["history", "--project", str(root), "--filter", "score=best"])
    assert result.exit_code == 1
    assert "Supported filters" in result.output


def test_projection_lock_does_not_allow_competing_writer(tmp_path):
    store = HistoryStore(tmp_path / "history.sqlite")
    with store.locked():
        with pytest.raises(ProjectionError, match="busy"):
            with store.locked():
                pytest.fail("Competing writer acquired the lock")
    assert not store.path.with_suffix(".lock").exists()


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_missing_database_does_not_overwrite_external_journal(tmp_path, suffix):
    store = HistoryStore(tmp_path / "history.sqlite")
    journal = tmp_path / ("history.sqlite" + suffix)
    journal.write_bytes(b"external journal")
    with store.locked(), pytest.raises(ProjectionError, match="external SQLite writers"):
        store.refresh([], "source", replace=True)
    assert not store.path.exists()
    assert journal.read_bytes() == b"external journal"


def test_intel_and_ticket_characteristics_are_sourced(history_project):
    root, path, state = history_project
    # Build before adding Intel to verify that changes to a second canonical
    # repository invalidate the index too.
    assert query_history(root, HistoryQuery(filters={"intel": "INS-SEARCH"}))["total"] == 0
    instinct = AcceptedInstinct(
        instinct_id="INS-SEARCH", lifecycle="accepted", recommendation="Keep parser evidence",
        evidence=[InstinctEvidenceReference(
            run_id="run-history", node_execution_id="review-1", reference="report.txt",
            description="Parser regression evidence",
        )],
        audience=["reviewer"], tags=["parser"],
        applicability=InstinctApplicability(description="Parser changes"),
        creation_provenance=InstinctCreationProvenance(
            originating_run_id="run-history", originating_node_execution_ids=["review-1"],
            created_at=FIXTURE_TIME, created_by="operator",
        ),
        acceptance_provenance=AcceptanceProvenance(accepted_at=FIXTURE_TIME, accepted_by="Human"),
    )
    IntelRepository(root / ".battalion/intel").store(instinct)
    state.work_item = WorkItem(
        source_integration_id="fixture", external_id="79", title="Parser history",
        labels=("bug",), provenance=WorkItemProvenance(retrieved_at=FIXTURE_TIME, operation="work.get"),
    )
    persisted_checkpoint(path, state)
    query = HistoryQuery(filters={"intel": "INS-SEARCH", "intel_tag": "parser", "label": "bug"})
    result = query_history(root, query)
    assert result["total"] == 1
    assert result["results"][0]["intel_evidence"][0]["reference"] == "report.txt"
    group = query_history(root, query, dimension="requested_model")["groups"][0]
    assert group["evidence"][0]["ticket"]["labels"] == ["bug"]
    assert query_history(root, HistoryQuery("Parser regression evidence"))["total"] == 1
    # Same role/model with unknown ticket characteristics must not join the
    # labeled ticket cohort merely because those observations share a model.
    legacy = state.model_copy(deep=True, update={"run_id": "legacy-ticket", "work_item": None})
    persisted_checkpoint(root / ".battalion/state/legacy-ticket.json", legacy)
    groups = query_history(root, dimension="requested_model")["groups"]
    assert len(groups) == 2
    assert sorted(group["attempts"] for group in groups) == [1, 1]
    assert {tuple(group["segments"]["ticket_labels"] or ()) for group in groups} == {(), ("bug",)}


def test_search_pagination_unknown_and_empty_results(history_project):
    root, path, _ = history_project
    all_results = query_history(root)
    first = query_history(root, HistoryQuery(limit=1))
    second = query_history(root, HistoryQuery(limit=1, offset=1))
    assert first["total"] == second["total"] == 2
    assert first["results"] + second["results"] == all_results["results"]
    assert query_history(root, HistoryQuery(filters={"role": None}))["results"] == first["results"]
    assert query_history(root, HistoryQuery(filters={"role": "driver"}), dimension="model")["groups"] == []
    path.unlink()
    assert query_history(root)["total"] == 0


def test_failed_publication_preserves_prior_projection(history_project, monkeypatch):
    root, _, _ = history_project
    result = query_history(root)
    projection = root / ".battalion/projections/history.sqlite"
    original = projection.read_bytes()
    import battalion.history_store as storage

    def fail_replace(source, destination):
        raise OSError("injected publication failure")

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail_replace)
        with pytest.raises(ApplicationError, match="injected publication failure"):
            query_history(root, rebuild=True)
    assert projection.read_bytes() == original
    assert query_history(root) == result
    assert sorted(item.name for item in projection.parent.iterdir()) == ["history.sha256", "history.sqlite"]


@pytest.mark.parametrize("query", [
    {"filters": {"unknown-field": "value"}}, {"limit": 0}, {"limit": 1001},
    {"offset": -1}, {"text": "x" * 2001},
], ids=["field", "zero-limit", "large-limit", "offset", "text-bound"])
def test_invalid_search_contract(query):
    with pytest.raises(ValueError):
        HistoryQuery(**query)


@pytest.mark.parametrize("lower,upper,total", [
    pytest.param("2026-07-31T17:00:00-07:00", "2026-08-01T00:00:00Z", 1, id="inclusive-offset"),
    pytest.param("2026-08-01T00:00:00.000001Z", None, 0, id="after-start"),
    pytest.param(None, "2026-07-31T23:59:59.999999Z", 0, id="before-start"),
])
def test_date_bounds_use_attempt_start_in_utc(history_project, lower, upper, total):
    root, _, _ = history_project
    query = HistoryQuery(date_from=datetime.fromisoformat(lower) if lower else None,
                         date_to=datetime.fromisoformat(upper) if upper else None)
    assert query_history(root, query)["total"] == total


def test_analytics_time_range_keeps_naive_history_unknown(history_project):
    root, path, state = history_project
    other = state.execution_record.node_executions[0].model_copy(deep=True)
    other.execution_id = "unknown-time"
    other.started_at = FIXTURE_TIME.replace(tzinfo=None)
    other.ended_at = other.started_at + timedelta(seconds=2)
    state.execution_record.node_executions.append(other)
    persisted_checkpoint(path, state)
    group = query_history(root, dimension="requested_model")["groups"][0]
    assert group["attempts"] == 2
    assert group["time_range"] == {
        "first_started_at": "2026-08-01T00:00:00.000000+00:00",
        "last_started_at": "2026-08-01T00:00:00.000000+00:00",
        "last_ended_at": "2026-08-01T00:00:04.000000+00:00",
    }
    assert group["unknown_start_time_attempts"] == 1
    assert {"context_policy", "project_domain"} <= set(group["unknown_segments"])
    assert query_history(root, HistoryQuery(date_from=FIXTURE_TIME))["total"] == 1


@pytest.mark.parametrize("currency,source,minimum,maximum,total", [
    pytest.param("USD", "provider-reported", "0.10", "0.10", 1, id="inclusive-subtotal"),
    pytest.param("USD", "estimated", "0.10", "0.10", 0, id="different-source"),
    pytest.param("EUR", "provider-reported", "0.10", "0.10", 0, id="different-currency"),
    pytest.param("USD", "provider-reported", "0.1000000000000000000000000001", None, 0, id="precise-lower"),
    pytest.param("USD", "provider-reported", None, "0.0999999999999999999999999999", 0, id="precise-upper"),
])
def test_cost_ranges_are_exact_sourced_subtotals(history_project, currency, source, minimum, maximum, total):
    root, _, _ = history_project
    query = HistoryQuery(cost_currency=currency, cost_source=source,
                         cost_min=Decimal(minimum) if minimum else None,
                         cost_max=Decimal(maximum) if maximum else None)
    assert query_history(root, query)["total"] == total


def test_cost_range_sums_calls_without_losing_precision(history_project):
    root, path, state = history_project
    attempt = state.execution_record.node_executions[0]
    attempt.llm_calls.append(LLMCallCost(
        call_id="precise", model="compatibility", requested_model="requested", input_tokens=0,
        output_tokens=0, cost="0.0000000000000000000000000001", cost_currency="USD",
        cost_source="provider-reported",
    ))
    attempt.llm_calls.append(LLMCallCost(call_id="missing", model="compatibility", requested_model="requested",
                                         input_tokens=0, output_tokens=0))
    persisted_checkpoint(path, state)
    subtotal = Decimal("0.1000000000000000000000000001")
    query = HistoryQuery(cost_currency="USD", cost_source="provider-reported", cost_min=subtotal, cost_max=subtotal)
    assert query_history(root, query)["total"] == 1
    group = query_history(root, query, dimension="requested_model")["groups"][0]
    assert group["costs"][0]["total"] == str(subtotal)
    assert group["unknown_cost_calls"] == 1


@pytest.mark.parametrize("field,value", [
    ("phase", "reviewer_red"), ("checkpoint", "red-check"),
    ("prompt_template_hash", "e" * 64), ("prompt_contract_version", "2.0"),
    ("battalion_revision", "f" * 40),
])
def test_analytics_separates_recorded_comparison_segments(history_project, field, value):
    root, path, state = history_project
    other = state.execution_record.node_executions[0].model_copy(deep=True)
    other.execution_id = "different-context"
    if field == "phase":
        other.phase = value
    elif field == "checkpoint":
        other.review_result = ReviewResult(checkpoint=value, verdict="accepted")
    else:
        attribute = {"prompt_template_hash": "template_hash", "prompt_contract_version": "contract_version",
                     "battalion_revision": "battalion_revision"}[field]
        other.prompt_provenance = other.prompt_provenance.model_copy(update={attribute: value})
    state.execution_record.node_executions.append(other)
    persisted_checkpoint(path, state)
    groups = query_history(root, dimension="requested_model")["groups"]
    assert len(groups) == 2
    assert all(group["attempts"] == 1 for group in groups)
    result = query_history(root, HistoryQuery(filters={field: value}))
    assert result["total"] == 1
    assert result["results"][0]["execution_id"] == "different-context"


@pytest.mark.parametrize("options,message", [
    ({"date_from": datetime(2026, 1, 1)}, "timezone"),
    ({"date_from": FIXTURE_TIME, "date_to": FIXTURE_TIME - timedelta(seconds=1)}, "date_from"),
    ({"cost_min": Decimal("NaN")}, "finite"),
    ({"cost_max": Decimal("Infinity")}, "finite"),
    ({"cost_min": Decimal("-1")}, "nonnegative"),
    ({"cost_min": Decimal("2"), "cost_max": Decimal("1")}, "cost_min"),
    ({"cost_min": Decimal("0")}, "currency"),
    ({"cost_currency": "USD", "cost_source": "unknown"}, "source"),
    ({"cost_currency": "usd", "cost_source": "estimated"}, "uppercase"),
], ids=["naive-date", "reversed-dates", "nan", "infinity", "negative", "reversed-cost",
        "missing-units", "unknown-source", "bad-currency"])
def test_range_filters_reject_ambiguous_queries(options, message):
    with pytest.raises(ValueError, match=message):
        HistoryQuery(**options)


def test_cli_range_filters_and_errors(history_project):
    root, _, _ = history_project
    runner = CliRunner()
    base = ["history", "--project", str(root)]
    result = runner.invoke(app, [*base, "--date-from", "2026-08-01T00:00:00Z",
                                "--cost-min", "0.10", "--cost-max", "0.10",
                                "--cost-currency", "USD", "--cost-source", "provider-reported"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["total"] == 1
    for option, value, message in [("--cost-min", "no", "decimal numbers"),
                                   ("--date-from", "2026-08-01", "timezone")]:
        result = runner.invoke(app, [*base, option, value])
        assert result.exit_code == 1
        assert message in result.output
