"""BTN-20 acceptance tests for the versioned Instinct contract."""

from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from battalion.intel.models import (
    AcceptedInstinct,
    AcceptanceProvenance,
    CandidateInstinct,
    Instinct,
    InstinctAudience,
    InstinctLifecycle,
)


def _candidate_data(**overrides):
    data = {
        "schema_version": "1.0",
        "instinct_id": "INS-SCOPED-WRITES",
        "lifecycle": "candidate",
        "recommendation": "Bind each writing role to its declared project-relative roots.",
        "evidence": [
            {
                "run_id": "run-BTN-19",
                "node_execution_id": "node-driver-green-1",
                "reference": "execution_record.node_executions[3]",
                "description": "The scoped write rejected an undeclared target before writing.",
            }
        ],
        "audience": ["driver", "refactorer"],
        "applicability": {
            "description": "Writing nodes operating on repository files.",
            "include": ["repositories with phase-specific write roots"],
            "exclude": ["read-only review nodes"],
        },
        "tags": ["write-scope", "safety"],
        "creation_provenance": {
            "originating_run_id": "run-BTN-19",
            "originating_node_execution_ids": ["node-driver-green-1"],
            "created_at": "2026-08-13T12:00:00Z",
            "created_by": "recon",
        },
    }
    data.update(overrides)
    return data


def test_candidate_contract_validates_all_required_context():
    instinct = CandidateInstinct.model_validate(_candidate_data())

    assert instinct.schema_version == "1.0"
    assert instinct.lifecycle is InstinctLifecycle.CANDIDATE
    assert instinct.audience == [InstinctAudience.DRIVER, InstinctAudience.REFACTORER]
    assert instinct.evidence[0].run_id == instinct.creation_provenance.originating_run_id


def test_candidate_and_accepted_are_discriminated_lifecycle_types():
    adapter = TypeAdapter(Instinct)
    candidate = adapter.validate_python(_candidate_data())
    assert isinstance(candidate, CandidateInstinct)

    accepted = adapter.validate_python({
        **_candidate_data(lifecycle="accepted"),
        "acceptance_provenance": {
            "accepted_at": "2026-08-13T13:00:00Z",
            "accepted_by": "operator@example.com",
        },
    })
    assert isinstance(accepted, AcceptedInstinct)


def test_candidate_cannot_masquerade_as_accepted_knowledge():
    with pytest.raises(ValidationError):
        AcceptedInstinct.model_validate(_candidate_data())

    with pytest.raises(ValidationError):
        CandidateInstinct.model_validate({
            **_candidate_data(),
            "acceptance_provenance": {
                "accepted_at": "2026-08-13T13:00:00Z",
                "accepted_by": "operator@example.com",
            },
        })


def test_accepted_instinct_requires_human_acceptance_provenance():
    provenance = AcceptanceProvenance(
        accepted_at=datetime(2026, 8, 13, 13, tzinfo=timezone.utc),
        accepted_by="operator@example.com",
    )
    instinct = AcceptedInstinct.model_validate({
        **_candidate_data(lifecycle="accepted"),
        "acceptance_provenance": provenance,
    })
    assert instinct.acceptance_provenance == provenance


@pytest.mark.parametrize("instinct_id", ["", "instinct-1", "INS x", "INS-"])
def test_instinct_identifier_has_a_stable_validated_format(instinct_id):
    with pytest.raises(ValidationError):
        CandidateInstinct.model_validate(_candidate_data(instinct_id=instinct_id))


def test_supersession_references_another_stable_identifier():
    instinct = AcceptedInstinct.model_validate({
        **_candidate_data(lifecycle="accepted", supersedes_id="INS-OLD-GUIDANCE"),
        "acceptance_provenance": {
            "accepted_at": "2026-08-13T13:00:00Z",
            "accepted_by": "operator@example.com",
        },
    })
    assert instinct.supersedes_id == "INS-OLD-GUIDANCE"

    with pytest.raises(ValidationError):
        CandidateInstinct.model_validate(
            _candidate_data(supersedes_id="INS-SCOPED-WRITES")
        )


@pytest.mark.parametrize("missing", ["recommendation", "evidence", "audience", "applicability"])
def test_accepted_instinct_cannot_omit_standalone_evidence_or_scope(missing):
    data = {
        **_candidate_data(lifecycle="accepted"),
        "acceptance_provenance": {
            "accepted_at": "2026-08-13T13:00:00Z",
            "accepted_by": "operator@example.com",
        },
    }
    data.pop(missing)
    with pytest.raises(ValidationError):
        AcceptedInstinct.model_validate(data)


def test_confidence_is_not_part_of_creation_contract():
    with pytest.raises(ValidationError):
        CandidateInstinct.model_validate(_candidate_data(confidence=0.9))
