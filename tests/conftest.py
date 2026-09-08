"""Pytest bootstrap. Import builders from support, not conftest."""

# Preserve the suite's existing initialization order: importing the LLM client
# first exposes a production cycle through execution, state and Tactician.
# Decoupling that cycle is separate from consolidating test construction.
import battalion.graph  # noqa: F401

import pytest


@pytest.fixture
def isolated_artifact_gate(monkeypatch):
    """Isolate pre-existing role/routing tests from artifact admission policy.

    Modules opt in explicitly. This supplies no fabricated semantic evidence;
    real admission, scoped-write, and persistence scenarios live in
    test_artifact_target_sealing and never request this fixture.
    """
    monkeypatch.setattr("battalion.graph.admit_driver_attempt", lambda state, **kwargs: state)
