"""Export the shared desktop benchmark bundle as framework-neutral JSON."""

from __future__ import annotations

import argparse
from pathlib import Path

from benchmarks.desktop.contract import write_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    for path in write_bundle(args.output):
        print(path)


if __name__ == "__main__":
    main()
