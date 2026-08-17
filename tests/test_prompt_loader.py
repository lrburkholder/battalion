"""Tests for battalion.prompts.loader — externalized per-node system
prompts, so prompt iteration is a config change, not a code change."""
import pytest

from battalion.prompts.loader import PromptNotFound, load_system_prompt


def test_load_system_prompt_reads_file(tmp_path):
    (tmp_path / "architect.md").write_text("You are the Architect. Be rigorous.")
    content = load_system_prompt("architect", prompts_dir=tmp_path)
    assert content == "You are the Architect. Be rigorous."


@pytest.mark.parametrize(
    ("node_name", "contents"),
    [("nonexistent-node", None), ("driver", "   ")],
    ids=["missing", "empty"],
)
def test_load_system_prompt_rejects_missing_or_empty_templates(
    tmp_path, node_name, contents
):
    if contents is not None:
        (tmp_path / f"{node_name}.md").write_text(contents)
    with pytest.raises(PromptNotFound):
        load_system_prompt(node_name, prompts_dir=tmp_path)


def test_load_system_prompt_default_dir_finds_repo_prompts():
    # No prompts_dir override -> resolves to the repo's top-level prompts/
    # directory, which should contain architect.md by the time BTN-4 is
    # retrofitted onto this loader.
    content = load_system_prompt("architect")
    assert content.strip() != ""
