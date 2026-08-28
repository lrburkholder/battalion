"""Externalized per-node system prompts.

Node prompts live in the repo's top-level prompts/ directory as plain text
(one file per node, e.g. prompts/architect.md), not as Python string
constants — iterating on a node's prompt should be a config change, not a
code change.
"""
from __future__ import annotations

from pathlib import Path

# battalion/battalion/prompts/loader.py -> repo root is two parents up from
# the package dir, one more up from this file's own directory.
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROMPTS_DIR = _REPO_ROOT / "prompts"

# Human-readable compatibility contracts are intentionally separate from the
# content hash. Bump one when the inputs, output shape, or authority expressed
# by a role prompt changes; ordinary wording changes are still distinguished by
# the exact template hash.
PROMPT_CONTRACT_VERSIONS = {
    "architect": "architect/v1",
    "driver": "driver/v1",
    "driver-red": "driver-red/v1",
    "driver-green": "driver-green/v1",
    "reviewer": "reviewer/v1",
    "refactorer": "refactorer/v2",
    "recon": "recon/v1",
}


class PromptNotFound(Exception):
    """Raised when a node's system prompt file is missing or empty."""


def prompt_contract_version(node_name: str) -> str:
    """Return the explicit human-readable contract for a prompt template."""
    try:
        return PROMPT_CONTRACT_VERSIONS[node_name]
    except KeyError as exc:
        raise PromptNotFound(
            f"No prompt contract version is declared for node '{node_name}'"
        ) from exc


def load_system_prompt(node_name: str, prompts_dir: str | Path | None = None) -> str:
    directory = Path(prompts_dir) if prompts_dir is not None else DEFAULT_PROMPTS_DIR
    path = directory / f"{node_name}.md"

    if not path.exists():
        raise PromptNotFound(
            f"No system prompt file found for node '{node_name}' at {path}"
        )

    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise PromptNotFound(
            f"System prompt file for node '{node_name}' at {path} is empty"
        )
    return content
