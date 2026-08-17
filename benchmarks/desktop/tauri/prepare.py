"""Export BTN-37 inputs for the disposable Tauri spike."""

from pathlib import Path

from benchmarks.desktop import write_bundle


SPIKE_ROOT = Path(__file__).resolve().parent


def prepare(output: Path | None = None) -> tuple[Path, Path, Path]:
    """Generate the unmodified shared inputs in the Tauri frontend."""
    return write_bundle(output or SPIKE_ROOT / "ui" / "benchmark-input")


if __name__ == "__main__":
    for path in prepare():
        print(path)

