"""Frozen detached-worker entry point for the split desktop distribution."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from battalion.prompts.smoke import main as smoke_prompts


PROMPT_SMOKE_ARGUMENT = "--smoke-role-prompts"


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == [PROMPT_SMOKE_ARGUMENT]:
        smoke_prompts()
        return 0
    if arguments:
        print(f"Unsupported worker arguments: {' '.join(arguments)}", file=sys.stderr)
        return 2

    from battalion.workers import _worker_main

    return _worker_main(sys.stdin.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
