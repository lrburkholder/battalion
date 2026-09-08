"""Tests for install-safe Battalion prompt resources and overrides."""

from importlib import resources

import pytest

from battalion.prompts.loader import (
    SHIPPED_PROMPT_NAMES,
    PromptNotFound,
    load_prompt_template,
    load_system_prompt,
)
from battalion.prompts.smoke import validate_shipped_prompts


def test_load_system_prompt_reads_file(tmp_path):
    (tmp_path / "architect.md").write_bytes(
        b"You are the Architect.\r\nBe rigorous."
    )
    content = load_system_prompt("architect", prompts_dir=tmp_path)
    assert content == "You are the Architect.\nBe rigorous."


@pytest.mark.parametrize(
    ("node_name", "contents"),
    [("architect", None), ("driver", "   ")],
    ids=["missing", "empty"],
)
def test_load_system_prompt_rejects_missing_or_empty_templates(
    tmp_path, node_name, contents
):
    if contents is not None:
        (tmp_path / f"{node_name}.md").write_text(contents)
    with pytest.raises(PromptNotFound) as exc_info:
        load_system_prompt(node_name, prompts_dir=tmp_path)
    if contents is None:
        message = str(exc_info.value)
        assert "incomplete" in message
        assert "omit --prompts-dir" in message


def test_packaged_prompt_inventory_matches_declared_contracts():
    packaged = {
        item.name.removesuffix(".md")
        for item in resources.files("battalion.prompts").iterdir()
        if item.name.endswith(".md")
    }

    assert packaged == set(SHIPPED_PROMPT_NAMES)
    assert validate_shipped_prompts() == SHIPPED_PROMPT_NAMES


def test_default_prompt_uses_stable_package_resource_provenance():
    template = load_prompt_template("architect")

    assert template.content.strip()
    assert template.source == "battalion/prompts/architect.md"
    assert template.content_bytes.decode("utf-8").replace("\r\n", "\n") == (
        template.content
    )
