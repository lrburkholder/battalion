"""Pytest bootstrap. Import builders from support, not conftest."""

# Preserve the suite's existing initialization order: importing the LLM client
# first exposes a production cycle through execution, state and Tactician.
# Decoupling that cycle is separate from consolidating test construction.
import battalion.graph  # noqa: F401
