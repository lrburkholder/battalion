"""Capture deterministic production desktop views for the public Pages site."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "docs" / "assets" / "screenshots"
VIEWS = ("work", "history", "intel")


def capture(output: Path) -> None:
    """Render credential-free fixture data through the production Qt window."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from battalion.desktop.app import BattalionWindow
    from battalion.desktop.demo import showcase_snapshot

    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication(sys.argv[:1])
    window = BattalionWindow(REPOSITORY_ROOT, autoload=False)
    window.resize(1380, 860)
    window.render_snapshot(*showcase_snapshot())
    window.actor_edit.setText("showcase-operator")
    window.intel_actor_edit.setText("showcase-operator")
    window.show()
    for view in VIEWS:
        window.select_showcase_view(view)
        application.processEvents()
        destination = output / f"battalion-{view}.png"
        if not window.grab().save(str(destination), "PNG", 88):
            raise RuntimeError(f"Could not save screenshot to {destination}")
    window.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    capture(args.output.resolve())


if __name__ == "__main__":
    main()
