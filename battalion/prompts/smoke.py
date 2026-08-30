"""Credential-free runtime smoke check for every shipped Battalion prompt."""

from __future__ import annotations

import json

from battalion.prompts.loader import SHIPPED_PROMPT_NAMES, load_system_prompt


def validate_shipped_prompts() -> tuple[str, ...]:
    """Load every declared prompt through the installed runtime boundary."""
    for name in SHIPPED_PROMPT_NAMES:
        load_system_prompt(name)
    return SHIPPED_PROMPT_NAMES


def main() -> None:
    names = validate_shipped_prompts()
    print(json.dumps({"prompt_names": names, "status": "ok"}, sort_keys=True))


if __name__ == "__main__":
    main()
