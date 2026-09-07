"""Pure, disposable history products derived from validated execution evidence."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, localcontext

from battalion.state.models import RunState


IDENTITY_FIELDS = (
    "model", "requested_model", "response_model", "routed_model",
    "routed_provider", "backend", "endpoint_url", "inference_location",
    "identity_contradiction",
)
FILTER_FIELDS = (*IDENTITY_FIELDS, "run_id", "ticket_id", "role", "phase",
                 "outcome", "run_status", "review", "interrupt", "artifact",
                 "reference", "intel", "label", "verification", "checkpoint",
                 "review_cause", "artifact_digest", "intel_tag", "project_id",
                 "prompt_template_hash", "prompt_contract_version", "battalion_revision",
                 "context_policy", "project_domain")
SEGMENT_FIELDS = ("phase", "checkpoint", "prompt_template_hash", "prompt_contract_version",
                  "battalion_revision", "context_policy", "project_id", "project_domain",
                  "ticket_labels")
ANALYTICS_NOTICE = (
    "Descriptive observations only; sample sizes and ticket characteristics confound "
    "comparisons. No effectiveness ranking or model recommendation is implied. "
    "Null means unknown. Multiple identity values describe a mixed attempt; "
    "attempt duration is not call latency. Costs are separated by currency and source. "
    "Grouping unknown segments does not establish that those attempts had comparable context."
)


@dataclass(frozen=True)
class HistoryQuery:
    text: str = ""
    filters: dict[str, str | None] = field(default_factory=dict)
    limit: int = 100
    offset: int = 0
    date_from: datetime | None = None
    date_to: datetime | None = None
    cost_min: Decimal | None = None
    cost_max: Decimal | None = None
    cost_currency: str | None = None
    cost_source: str | None = None

    def __post_init__(self) -> None:
        if self.filters.keys() - set(FILTER_FIELDS):
            raise ValueError(f"Supported filters: {', '.join(FILTER_FIELDS)}")
        if not 1 <= self.limit <= 1000 or self.offset < 0:
            raise ValueError("limit must be 1..1000 and offset must be nonnegative")
        if len(self.text) > 2000:
            raise ValueError("Search text must not exceed 2000 characters")
        for value in (self.date_from, self.date_to):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("Date bounds require an explicit timezone")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must not exceed date_to")
        for value in (self.cost_min, self.cost_max):
            if value is not None and (not value.is_finite() or value < 0):
                raise ValueError("Cost bounds must be finite nonnegative decimals")
        if self.cost_min is not None and self.cost_max is not None and self.cost_min > self.cost_max:
            raise ValueError("cost_min must not exceed cost_max")
        if any(value is not None for value in (self.cost_min, self.cost_max, self.cost_currency, self.cost_source)):
            if (self.cost_currency is None or len(self.cost_currency) != 3
                    or not self.cost_currency.isascii() or not self.cost_currency.isalpha()
                    or not self.cost_currency.isupper()
                    or self.cost_source not in {"provider-reported", "estimated"}):
                raise ValueError("Cost filtering requires a three-letter uppercase currency and provider-reported or estimated source")

    def selection(self) -> dict:
        return {
            "text": self.text, "filters": dict(self.filters),
            "date_from": utc_timestamp(self.date_from), "date_to": utc_timestamp(self.date_to),
            "date_basis": "attempt start, inclusive UTC bounds; unknown starts excluded when bounded",
            "cost_min": str(self.cost_min) if self.cost_min is not None else None,
            "cost_max": str(self.cost_max) if self.cost_max is not None else None,
            "cost_currency": self.cost_currency, "cost_source": self.cost_source,
            "cost_basis": "observed attempt subtotal in the selected currency/source; unknown costs excluded from subtotal",
        }


def utc_timestamp(value: datetime | None) -> str | None:
    """Never assign a timezone to historical evidence that does not have one."""
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def cost_totals(calls: list[dict]) -> list[dict]:
    buckets = defaultdict(list)
    for call in calls:
        if call["cost"] is not None:
            buckets[(call["cost_currency"], call["cost_source"])].append(Decimal(call["cost"]))
    return [{"currency": currency, "source": source, "total": str(_exact_sum(amounts)),
             "observations": len(amounts)}
            for (currency, source), amounts in sorted(buckets.items())]


def _exact_sum(amounts: list[Decimal]) -> Decimal:
    """Preserve sourced decimal precision, including very small currency units."""
    lowest = min(amount.as_tuple().exponent for amount in amounts)
    highest = max(amount.adjusted() for amount in amounts)
    with localcontext() as context:
        context.prec = max(1, highest - lowest + len(str(len(amounts))) + 2)
        return sum(amounts, Decimal(0))


def _search_text(value) -> str:
    """Index evidence values without JSON escaping paths, quotes, or newlines."""
    if isinstance(value, dict):
        return _search_text(list(value.values()))
    if isinstance(value, (list, tuple)):
        return "\n".join(_search_text(item) for item in value)
    return "" if value is None else str(value).casefold()


def project_run(state: RunState, source_path: str) -> list[dict]:
    """Keep run and attempt evidence distinct, with original source pointers."""
    work = state.work_item
    common = {
        "run_id": state.run_id, "ticket_id": state.ticket_id,
        "run_status": state.status.value, "source_path": source_path,
        "ticket": work.model_dump(mode="json") if work else None,
        "label": list(work.labels) if work else [],
        "project_id": state.project_id,
        "project_domain": None,
        "ticket_labels": sorted(work.labels) if work else None,
    }
    run = dict(common, execution_id=None, phase=state.phase, role=None,
               outcome=None, interrupt=[entry.trigger for entry in state.interrupt_log])
    run["search_text"] = _search_text([
        state.ticket_id, state.run_alias, state.spec[:50000], run["ticket"],
        state.status.value, state.phase,
        [entry.model_dump(mode="json") for entry in state.interrupt_log],
    ])
    rows = [run]
    for attempt in state.execution_record.node_executions:
        calls = [call.model_dump(mode="json") for call in attempt.llm_calls]
        summary = attempt.operator_summary
        prompt = attempt.prompt_provenance
        duration = None
        if attempt.ended_at is not None:
            try:
                seconds = (attempt.ended_at - attempt.started_at).total_seconds()
                if seconds >= 0:
                    duration = seconds
            except TypeError:
                pass  # Historical timestamps with mixed timezone evidence.
        row = dict(
            common, execution_id=attempt.execution_id, role=attempt.role,
            phase=attempt.phase, outcome=attempt.outcome, calls=calls,
            review=attempt.review_result.verdict if attempt.review_result else None,
            review_cause=attempt.review_result.cause if attempt.review_result else None,
            checkpoint=sorted({item.checkpoint.value for item in
                               (attempt.review_result, attempt.test_outcome) if item is not None}),
            artifact_digest=sorted({item.sha256 for item in attempt.artifact_provenance}),
            prompt_template_hash=prompt.template_hash if prompt else None,
            prompt_contract_version=prompt.contract_version if prompt else None,
            battalion_revision=prompt.battalion_revision if prompt else None,
            context_policy=None,
            started_at=utc_timestamp(attempt.started_at),
            ended_at=utc_timestamp(attempt.ended_at),
            timestamp_evidence={"started_at": attempt.started_at.isoformat(),
                                "ended_at": attempt.ended_at.isoformat() if attempt.ended_at else None},
            cost_totals=cost_totals(calls),
            duration_seconds=duration,
            summary=summary.model_dump(mode="json") if summary else None,
            verification=attempt.test_execution.classification.value
            if attempt.test_execution else None,
            interrupt=[entry.trigger for entry in state.interrupt_log
                       if entry.node_execution_id == attempt.execution_id],
            artifact=sorted(set(
                [item.path for item in attempt.artifact_provenance]
                + (summary.artifact_paths if summary else [])
                + ([attempt.output_reference] if attempt.output_reference else [])
            )),
            reference=[item.reference for item in attempt.input_references],
        )
        for dimension in IDENTITY_FIELDS:
            row[dimension] = list(dict.fromkeys(call[dimension] for call in calls)) or [None]
        # This is only the original compatibility identity, never a typed identity fallback.
        if not calls:
            row["model"] = [attempt.model_identity]
        row["search_text"] = _search_text([
            run["search_text"], attempt.model_dump(mode="json"),
        ])
        rows.append(row)
    return rows


def link_intel(rows: list[dict], instincts) -> None:
    """Link only explicit Intel evidence pointers, never infer retrieval/use."""
    links = defaultdict(list)
    for instinct in instincts:
        for evidence in instinct.evidence:
            links[(evidence.run_id, evidence.node_execution_id)].append({
                "instinct_id": instinct.instinct_id,
                "lifecycle": instinct.lifecycle.value,
                "reference": evidence.reference,
                "description": evidence.description,
                "tags": list(instinct.tags),
            })
    for row in rows:
        evidence = links[(row["run_id"], row["execution_id"])]
        row["intel"] = sorted({item["instinct_id"] for item in evidence})
        row["intel_tag"] = sorted({tag for item in evidence for tag in item["tags"]})
        row["intel_evidence"] = evidence
        row["search_text"] += "\n" + _search_text(evidence)


def aggregate_attempts(rows: list[dict], dimension: str) -> dict:
    """Count each attempt once, including multi-call attempts with mixed identity."""
    if dimension not in IDENTITY_FIELDS:
        raise ValueError(f"Dimension must be one of: {', '.join(IDENTITY_FIELDS)}")
    groups = defaultdict(list)
    for row in rows:
        if row["execution_id"] is not None:
            values = tuple(sorted(set(row[dimension]), key=lambda value: (value is not None, value or "")))
            segments = tuple(tuple(row[name]) if isinstance(row[name], list) else row[name]
                             for name in SEGMENT_FIELDS)
            groups[(row["role"], values, segments)].append(row)
    output = []
    for (role, values, segments), attempts in sorted(groups.items(), key=lambda item: repr(item[0])):
        calls = [call for row in attempts for call in row["calls"]]
        durations = [row["duration_seconds"] for row in attempts if row["duration_seconds"] is not None]
        starts = [row["started_at"] for row in attempts if row["started_at"] is not None]
        ends = [row["ended_at"] for row in attempts if row["ended_at"] is not None]
        output.append({
            "role": role, "identity_values": list(values),
            "segments": {name: list(value) if isinstance(value, tuple) else value
                         for name, value in zip(SEGMENT_FIELDS, segments)},
            "unknown_segments": [name for name, value in zip(SEGMENT_FIELDS, segments)
                                 if value is None or (name == "checkpoint" and not value)],
            "time_range": {"first_started_at": min(starts) if starts else None,
                           "last_started_at": max(starts) if starts else None,
                           "last_ended_at": max(ends) if ends else None},
            "unknown_start_time_attempts": len(attempts) - len(starts),
            "unknown_end_time_attempts": len(attempts) - len(ends),
            "identity_status": "mixed" if len(values) > 1 else "unknown" if values == (None,) else "observed",
            "attempts": len(attempts), "runs": len({row["run_id"] for row in attempts}),
            "outcomes": dict(Counter(row["outcome"] for row in attempts)),
            "reviews": dict(Counter(row["review"] or "unknown" for row in attempts)),
            "observed_calls": len(calls),
            "attempts_without_call_evidence": sum(not row["calls"] for row in attempts),
            "input_tokens": sum(call["input_tokens"] for call in calls) if calls else None,
            "output_tokens": sum(call["output_tokens"] for call in calls) if calls else None,
            "costs": cost_totals(calls),
            "unknown_cost_calls": sum(call["cost"] is None for call in calls),
            "duration_seconds": sum(durations) if durations else None,
            "duration_observations": len(durations),
            "unknown_duration_attempts": len(attempts) - len(durations),
            "evidence": [{"run_id": row["run_id"], "execution_id": row["execution_id"],
                          "source_path": row["source_path"], "ticket_id": row["ticket_id"],
                          "ticket": row["ticket"]} for row in attempts],
        })
    return {"dimension": dimension, "notice": ANALYTICS_NOTICE, "groups": output}
