"""Export BTN-37 inputs for the disposable Electron spike."""

from pathlib import Path

from benchmarks.desktop import write_bundle


SPIKE_ROOT = Path(__file__).resolve().parent


def prepare(output: Path | None = None) -> tuple[Path, Path, Path]:
    return write_bundle(output or SPIKE_ROOT / "benchmark-input")


if __name__ == "__main__":
    for path in prepare():
        print(path)

