"""BTN-21 acceptance tests for immutable local Intel persistence."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from battalion.intel import (
    AcceptedInstinct,
    CandidateInstinct,
    ImmutableInstinctError,
    IntelRepository,
    InstinctNotFoundError,
)


def _instinct_data(instinct_id: str = "INS-SCOPED-WRITES", **overrides):
    data = {
        "schema_version": "1.0",
        "instinct_id": instinct_id,
        "lifecycle": "accepted",
        "recommendation": "Bind writing roles to declared project-relative roots.",
        "evidence": [
            {
                "run_id": "run-BTN-19",
                "node_execution_id": "node-driver-green-1",
                "reference": "execution_record.node_executions[3]",
                "description": "An undeclared write was rejected before execution.",
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
        "acceptance_provenance": {
            "accepted_at": "2026-08-13T13:00:00Z",
            "accepted_by": "operator@example.com",
        },
    }
    data.update(overrides)
    return data


def _accepted(instinct_id: str = "INS-SCOPED-WRITES", **overrides):
    return AcceptedInstinct.model_validate(_instinct_data(instinct_id, **overrides))


def test_store_and_reload_accepted_instinct(tmp_path):
    repository = IntelRepository(tmp_path / "intel")
    instinct = _accepted()

    repository.store(instinct)

    reloaded = IntelRepository(tmp_path / "intel")
    assert reloaded.get(instinct.instinct_id) == instinct
    assert reloaded.list_all() == [instinct]


def test_candidate_cannot_be_stored(tmp_path):
    repository = IntelRepository(tmp_path / "intel")
    candidate_data = _instinct_data(lifecycle="candidate")
    candidate_data.pop("acceptance_provenance")
    candidate = CandidateInstinct.model_validate(candidate_data)

    with pytest.raises(TypeError, match="accepted"):
        repository.store(candidate)  # type: ignore[arg-type]
    assert repository.list_all() == []


def test_existing_identifier_cannot_be_overwritten_even_with_same_content(tmp_path):
    repository = IntelRepository(tmp_path / "intel")
    original = _accepted()
    repository.store(original)

    with pytest.raises(ImmutableInstinctError, match=original.instinct_id):
        repository.store(original)

    assert repository.get(original.instinct_id) == original


def test_new_instinct_supersedes_without_mutating_history(tmp_path):
    repository = IntelRepository(tmp_path / "intel")
    original = _accepted()
    replacement = _accepted(
        "INS-SCOPED-WRITES-V2",
        recommendation="Bind every write operation to its phase-specific roots.",
        supersedes_id=original.instinct_id,
    )

    repository.store(original)
    repository.store(replacement)

    assert repository.get(original.instinct_id) == original
    assert repository.get(replacement.instinct_id) == replacement
    assert {item.instinct_id for item in repository.list_all()} == {
        original.instinct_id,
        replacement.instinct_id,
    }
    assert repository.list_active() == [replacement]


def test_supersession_must_reference_a_stored_instinct(tmp_path):
    repository = IntelRepository(tmp_path / "intel")
    replacement = _accepted(
        "INS-SCOPED-WRITES-V2",
        supersedes_id="INS-MISSING-GUIDANCE",
    )

    with pytest.raises(InstinctNotFoundError, match="INS-MISSING-GUIDANCE"):
        repository.store(replacement)


def test_malformed_persisted_record_fails_validation(tmp_path):
    root = tmp_path / "intel"
    root.mkdir()
    (root / "INS-BROKEN.json").write_text(json.dumps({"lifecycle": "accepted"}))

    with pytest.raises(ValidationError):
        IntelRepository(root).get("INS-BROKEN")


def test_persisted_identifier_must_match_filename(tmp_path):
    root = tmp_path / "intel"
    root.mkdir()
    (root / "INS-WRONG-NAME.json").write_text(_accepted().model_dump_json())

    with pytest.raises(ValueError, match="does not match"):
        IntelRepository(root).get("INS-WRONG-NAME")
