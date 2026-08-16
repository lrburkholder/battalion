"""BTN-32 run and project identity contract tests."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from battalion.identity import (
    DuplicateProjectIdentity,
    MalformedIdentity,
    ProjectCatalogMismatch,
    generate_run_identity,
    load_project_identity,
    load_run_catalog,
    open_projects,
    register_run,
)
from battalion.state.models import Budget, RunState, RunStatus
from battalion.state.persistence import save_state


NOW = datetime(2026, 8, 15, tzinfo=timezone.utc)


def make_state(
    *, run_id: str, ticket_id: str, project_id: str | None, run_alias: str | None
) -> RunState:
    return RunState(
        schema_version="1.0",
        run_id=run_id,
        run_alias=run_alias,
        project_id=project_id,
        ticket_id=ticket_id,
        status=RunStatus.NOT_STARTED,
        phase="architect",
        retry_bound=2,
        budget=Budget(limit=10),
    )


def test_new_run_ids_are_uuid_canonical_ids_independent_of_display_aliases():
    first = generate_run_identity("BTN-32")
    second = generate_run_identity("BTN-32")

    assert UUID(first.run_id).version == 4
    assert UUID(second.run_id).version == 4
    assert first.run_id != second.run_id
    assert first.display_alias.startswith("BTN-32-")


def test_duplicate_display_aliases_do_not_collide_in_the_run_catalog(tmp_path):
    project = load_project_identity(tmp_path, create=True, now=lambda: NOW)
    first = generate_run_identity("BTN-32")
    second = generate_run_identity("BTN-32")
    alias = "BTN-32-demo"

    register_run(
        make_state(
            run_id=first.run_id,
            ticket_id="BTN-32",
            project_id=str(project.project_id),
            run_alias=alias,
        ),
        tmp_path,
    )
    register_run(
        make_state(
            run_id=second.run_id,
            ticket_id="BTN-32",
            project_id=str(project.project_id),
            run_alias=alias,
        ),
        tmp_path,
    )

    catalog = load_run_catalog(tmp_path)
    assert {entry.run_id for entry in catalog.runs} == {first.run_id, second.run_id}
    assert [entry.display_alias for entry in catalog.runs] == [alias, alias]


def test_project_identity_survives_a_repository_move(tmp_path):
    original = tmp_path / "before"
    moved = tmp_path / "after"
    original.mkdir()
    identity = load_project_identity(original, create=True, now=lambda: NOW)
    run = generate_run_identity("BTN-32")
    register_run(
        make_state(
            run_id=run.run_id,
            ticket_id="BTN-32",
            project_id=str(identity.project_id),
            run_alias=run.display_alias,
        ),
        original,
    )

    original.rename(moved)

    assert load_project_identity(moved).project_id == identity.project_id
    assert load_run_catalog(moved).runs[0].run_id == run.run_id


def test_opening_two_live_copies_requires_explicit_reconciliation(tmp_path):
    original = tmp_path / "original"
    copied = tmp_path / "copied"
    original.mkdir()
    load_project_identity(original, create=True, now=lambda: NOW)
    shutil.copytree(original, copied)

    with pytest.raises(DuplicateProjectIdentity):
        open_projects([original, copied])


def test_legacy_human_readable_run_ids_remain_discoverable(tmp_path):
    project = load_project_identity(tmp_path, create=True, now=lambda: NOW)
    legacy = make_state(
        run_id="run-BTN-9",
        ticket_id="BTN-9",
        project_id=None,
        run_alias=None,
    )
    state_path = tmp_path / ".battalion" / "state" / "run-BTN-9.json"
    save_state(legacy, state_path)

    catalog = load_run_catalog(tmp_path, discover_legacy=True)

    assert catalog.project_id == project.project_id
    assert catalog.runs[0].run_id == "run-BTN-9"
    assert catalog.runs[0].display_alias == "run-BTN-9"
    assert catalog.runs[0].legacy_id is True


def test_malformed_project_identity_is_not_silently_replaced(tmp_path):
    marker = tmp_path / ".battalion" / "project.json"
    marker.parent.mkdir()
    marker.write_text('{"project_id": "not-a-uuid"}', encoding="utf-8")

    with pytest.raises(MalformedIdentity):
        load_project_identity(tmp_path, create=True)


def test_malformed_project_reference_in_run_state_is_rejected():
    with pytest.raises(ValidationError):
        make_state(
            run_id="run-legacy",
            ticket_id="BTN-32",
            project_id="not-a-uuid",
            run_alias="legacy",
        )


def test_run_catalog_cannot_cross_project_identity_boundary(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    first_identity = load_project_identity(first, create=True, now=lambda: NOW)
    second_identity = load_project_identity(second, create=True, now=lambda: NOW)
    run = generate_run_identity("BTN-32")
    state = make_state(
        run_id=run.run_id,
        ticket_id="BTN-32",
        project_id=str(first_identity.project_id),
        run_alias=run.display_alias,
    )

    with pytest.raises(ProjectCatalogMismatch):
        register_run(state, second)

    assert first_identity.project_id != second_identity.project_id
    assert load_run_catalog(first).runs == []
    assert load_run_catalog(second).runs == []
