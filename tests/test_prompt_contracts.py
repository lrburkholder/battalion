"""Static contracts for role prompts that are parsed or enforced by nodes."""

import pytest

from battalion.prompts.loader import load_system_prompt


@pytest.mark.parametrize(
    "name",
    ["driver", "driver-red", "driver-green", "refactorer"],
)
def test_file_producing_prompts_match_json_and_path_contract(name):
    prompt = load_system_prompt(name)
    normalized = " ".join(prompt.split())

    assert '"files"' in prompt
    assert "relative to the node's `src/` write root" in normalized
    assert "prefix paths with `src/`" in normalized
    assert "do not" in prompt.lower()
    assert "Return JSON only" in prompt


def test_red_and_green_prompts_preserve_mode_authority():
    red = load_system_prompt("driver-red")
    green = load_system_prompt("driver-green")

    assert "Do not write or modify production implementation" in red
    assert "must not be modified" in green
    assert "No returned basename may start with `test_`" in green


def test_reviewer_prompt_matches_stored_cause_contract():
    prompt = load_system_prompt("reviewer")

    assert "exactly one plain-text root-cause sentence" in prompt
    assert "no more than 30 words" in prompt
    assert "do not include test\nlogs" in prompt
    assert "Tests:" not in prompt


def test_architect_prompt_requires_evidence_bounded_plan():
    prompt = load_system_prompt("architect")

    assert "Do not invent requirements" in prompt
    assert "Do not fill the gap with a generic architecture" in prompt
    assert "Output only the plan content suitable for `plan.md`" in prompt


def test_refactorer_prompt_preserves_behavior_and_architecture():
    prompt = load_system_prompt("refactorer")

    assert "without changing observable\nbehavior" in prompt
    assert "Skip changes that require an Architect" in prompt
    assert "Do not claim that tests were executed" in prompt
