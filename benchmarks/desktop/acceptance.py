"""Validate a framework spike's JSON trace against the shared scenario."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmarks.desktop.contract import BenchmarkTrace, validate_trace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    args = parser.parse_args()
    trace = BenchmarkTrace.model_validate(json.loads(args.trace.read_text(encoding="utf-8")))
    validate_trace(trace)
    print(f"PASS: {trace.framework} completed BTN-37-desktop-v1")


if __name__ == "__main__":
    main()
