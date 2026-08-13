"""Post-completion Recon candidate generation (BTN-22)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from battalion.intel.models import AcceptedInstinct, CandidateInstinct
from battalion.llm.litellm_client import NodeLLMConfig, call_llm
from battalion.llm.response import extract_content
from battalion.prompts.loader import load_system_prompt
from battalion.state.models import RunState, RunStatus


_FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)


class ReconRequiresCompletedRun(ValueError):
    """Raised when Recon is asked to inspect an unfinished execution."""


class MalformedReconOutput(ValueError):
    """Raised when Recon output is not a valid candidate collection."""


class InvalidReconEvidence(ValueError):
    """Raised when a candidate cites evidence outside the supplied record."""


def _parse_candidates(response: Any) -> list[CandidateInstinct]:
    content = extract_content(response)
    if not isinstance(content, str):
        raise MalformedReconOutput("Recon output content must be text")
    match = _FENCE_RE.match(content.strip())
    try:
        value = json.loads(match.group(1) if match else content)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MalformedReconOutput(f"Recon output was not valid JSON: {exc}") from exc

    if not isinstance(value, dict) or set(value) != {"candidates"}:
        raise MalformedReconOutput(
            "Recon output must be an object containing only 'candidates'"
        )
    if not isinstance(value["candidates"], list):
        raise MalformedReconOutput("Recon 'candidates' must be a JSON array")
    try:
        return [CandidateInstinct.model_validate(item) for item in value["candidates"]]
    except ValidationError as exc:
        raise MalformedReconOutput(
            f"Recon emitted a candidate that violates the Instinct contract: {exc}"
        ) from exc


def _normalized(text: str) -> str:
    return " ".join(text.casefold().split())


def _is_duplicate(
    candidate: CandidateInstinct, accepted: Sequence[AcceptedInstinct]
) -> bool:
    recommendation = _normalized(candidate.recommendation)
    return any(
        candidate.instinct_id == instinct.instinct_id
        or recommendation == _normalized(instinct.recommendation)
        for instinct in accepted
    )


def _validate_evidence(candidate: CandidateInstinct, state: RunState) -> None:
    executions = {
        execution.execution_id: index
        for index, execution in enumerate(state.execution_record.node_executions)
    }
    if candidate.creation_provenance.originating_run_id != state.run_id:
        raise InvalidReconEvidence(
            f"candidate {candidate.instinct_id} provenance does not match run {state.run_id}"
        )
    if candidate.creation_provenance.created_by != "recon":
        raise InvalidReconEvidence(
            f"candidate {candidate.instinct_id} was not attributed to Recon"
        )
    if any(
        execution_id not in executions
        for execution_id in candidate.creation_provenance.originating_node_execution_ids
    ):
        raise InvalidReconEvidence(
            f"candidate {candidate.instinct_id} provenance cites an unknown node execution"
        )
    for evidence in candidate.evidence:
        index = executions.get(evidence.node_execution_id)
        expected = (
            f"execution_record.node_executions[{index}]" if index is not None else None
        )
        if evidence.run_id != state.run_id or evidence.reference != expected:
            raise InvalidReconEvidence(
                f"candidate {candidate.instinct_id} cites evidence outside the completed execution"
            )


def run_recon(
    state: RunState,
    accepted_instincts: Sequence[AcceptedInstinct],
    llm_config: NodeLLMConfig,
    call_llm_fn: Callable = call_llm,
    system_prompt: str | None = None,
    prompts_dir: str | Path | None = None,
    on_stream: Callable[[dict], None] | None = None,
) -> list[CandidateInstinct]:
    """Generate untrusted candidates from a completed durable execution record.

    This function deliberately returns candidates separately from ``RunState``.
    It has no write tools and no Intel repository dependency, so Recon cannot
    alter the completed run or publish institutional knowledge.
    """
    if state.status is not RunStatus.DONE or state.phase != "done":
        raise ReconRequiresCompletedRun("Recon can run only after execution completes")
    if not state.execution_record.node_executions:
        raise ReconRequiresCompletedRun(
            "Recon requires the completed run's durable execution record"
        )
    for instinct in accepted_instincts:
        if not isinstance(instinct, AcceptedInstinct):
            raise TypeError("accepted_instincts must contain only AcceptedInstinct records")

    prompt = system_prompt or load_system_prompt("recon", prompts_dir=prompts_dir)
    payload = {
        "run_id": state.run_id,
        "execution_record": state.execution_record.model_dump(mode="json"),
        "accepted_instincts_for_duplicate_comparison": [
            instinct.model_dump(mode="json") for instinct in accepted_instincts
        ],
    }
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, sort_keys=True)},
    ]
    if on_stream is None:
        response = call_llm_fn("recon", llm_config, messages)
    else:
        response = call_llm_fn("recon", llm_config, messages, on_stream=on_stream)

    candidates = _parse_candidates(response)
    seen_ids: set[str] = set()
    result: list[CandidateInstinct] = []
    for candidate in candidates:
        if candidate.instinct_id in seen_ids:
            raise MalformedReconOutput(
                f"Recon emitted duplicate candidate identifier {candidate.instinct_id}"
            )
        seen_ids.add(candidate.instinct_id)
        _validate_evidence(candidate, state)
        if not _is_duplicate(candidate, accepted_instincts):
            result.append(candidate)
    return result
