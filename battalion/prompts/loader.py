"""Install-safe access to Battalion-owned and operator-supplied role prompts."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path


# Human-readable compatibility contracts are intentionally separate from the
# content hash. Bump one when the inputs, output shape, or authority expressed
# by a role prompt changes; ordinary wording changes are still distinguished by
# the exact template hash.
PROMPT_CONTRACT_VERSIONS = {
    "architect": "architect/v1",
    "driver": "driver/v1",
    "driver-red": "driver-red/v2",
    "driver-green": "driver-green/v2",
    "reviewer": "reviewer/v1",
    "refactorer": "refactorer/v3",
    "recon": "recon/v1",
    "tactician": "tactician/v1",
}

# This inventory is consumed by source, wheel, sdist, and frozen-worker smoke
# tests. Adding a contract therefore also adds an artifact requirement instead
# of relying on a second manually maintained role list.
SHIPPED_PROMPT_NAMES = tuple(PROMPT_CONTRACT_VERSIONS)
PACKAGED_PROMPTS_PATH = "battalion/prompts"


class PromptNotFound(Exception):
    """Raised when a required system prompt cannot be loaded."""


@dataclass(frozen=True)
class PromptTemplate:
    """Exact prompt bytes plus their stable provenance label."""

    name: str
    content: str
    content_bytes: bytes
    source: str


def prompt_contract_version(node_name: str) -> str:
    """Return the explicit human-readable contract for a prompt template."""
    try:
        return PROMPT_CONTRACT_VERSIONS[node_name]
    except KeyError as exc:
        raise PromptNotFound(
            f"No prompt contract version is declared for node '{node_name}'"
        ) from exc


def _decode_prompt(node_name: str, data: bytes, source: str, *, override: bool) -> str:
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        kind = "Prompt override" if override else "Packaged prompt"
        raise PromptNotFound(
            f"{kind} for node '{node_name}' at {source} is not valid UTF-8"
        ) from exc
    # Match Path.read_text/open's universal-newline behavior so moving defaults
    # behind importlib.resources does not change the prompt supplied to a model.
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    if not content.strip():
        kind = "Prompt override" if override else "Packaged prompt"
        raise PromptNotFound(
            f"{kind} for node '{node_name}' at {source} is empty"
        )
    return content


def load_prompt_template(
    node_name: str, prompts_dir: str | Path | None = None
) -> PromptTemplate:
    """Load one required prompt from an override directory or package data.

    An explicit override is authoritative: Battalion never fills a missing
    override file from packaged defaults, because doing so would hide an
    incomplete operator configuration.
    """
    prompt_contract_version(node_name)
    filename = f"{node_name}.md"

    if prompts_dir is not None:
        directory = Path(prompts_dir)
        path = directory / filename
        if not path.is_file():
            raise PromptNotFound(
                f"Prompt override directory '{directory}' is incomplete: required "
                f"file '{filename}' was not found. Add the file or omit "
                "--prompts-dir to use Battalion-owned packaged prompts."
            )
        data = path.read_bytes()
        source = path.resolve().as_posix()
        return PromptTemplate(
            name=node_name,
            content=_decode_prompt(node_name, data, source, override=True),
            content_bytes=data,
            source=source,
        )

    resource = resources.files("battalion.prompts").joinpath(filename)
    source = f"{PACKAGED_PROMPTS_PATH}/{filename}"
    if not resource.is_file():
        raise PromptNotFound(
            f"Battalion's installation is incomplete: packaged prompt '{source}' "
            "is missing. Reinstall Battalion from a complete release artifact."
        )
    data = resource.read_bytes()
    return PromptTemplate(
        name=node_name,
        content=_decode_prompt(node_name, data, source, override=False),
        content_bytes=data,
        source=source,
    )


def load_system_prompt(node_name: str, prompts_dir: str | Path | None = None) -> str:
    """Load the UTF-8 text for one declared role prompt."""
    return load_prompt_template(node_name, prompts_dir=prompts_dir).content
