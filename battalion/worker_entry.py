"""Frozen detached-worker entry point for the split desktop distribution."""

from __future__ import annotations

import sys

from battalion.workers import _worker_main


def main() -> int:
    return _worker_main(sys.stdin.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
