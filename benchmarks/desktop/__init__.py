"""Shared BTN-37 desktop framework benchmark fixture."""

from benchmarks.desktop.contract import (
    BenchmarkBundle,
    BenchmarkTrace,
    build_bundle,
    validate_trace,
    write_bundle,
)

__all__ = [
    "BenchmarkBundle",
    "BenchmarkTrace",
    "build_bundle",
    "validate_trace",
    "write_bundle",
]
