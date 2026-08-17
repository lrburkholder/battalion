"""Disposable Qt Widgets UI for the BTN-37 desktop benchmark."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

try:
    from .adapter import run_scenario
except ImportError:  # Direct-script entry point used by pyside6-deploy.
    from adapter import run_scenario


SPIKE_ROOT = Path(__file__).resolve().parent


class BenchmarkWindow(QMainWindow):
    def __init__(self, fixture: dict[str, Any], scenario: list[dict[str, Any]]) -> None:
        super().__init__()
        self.trace = run_scenario(fixture, scenario)
        self.setWindowTitle("Battalion PySide6 Benchmark")
        self.setMinimumSize(760, 520)
        self.resize(1180, 760)
        icon = SPIKE_ROOT / "assets" / "app-icon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.setCentralWidget(self._content(scenario))
        self.setStyleSheet(STYLESHEET)

    def _content(self, scenario: list[dict[str, Any]]) -> QWidget:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(40, 34, 40, 40)
        layout.setSpacing(16)

        eyebrow = QLabel("DISPOSABLE FRAMEWORK SPIKE · BTN-39")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("Battalion operator benchmark")
        title.setObjectName("title")
        status = QLabel(f"{len(self.trace['entries'])} of {len(scenario)} steps complete")
        status.setAccessibleName("Benchmark completion status")
        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(status)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._scenario_panel())
        splitter.addWidget(self._trace_panel())
        splitter.setSizes([420, 700])
        layout.addWidget(splitter, 1)
        return root

    def _scenario_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        heading = QLabel("Shared scenario")
        heading.setObjectName("sectionTitle")
        steps = QListWidget()
        steps.setAccessibleName("Completed shared benchmark steps")
        steps.setObjectName("steps")
        for index, entry in enumerate(self.trace["entries"], start=1):
            item = QListWidgetItem(f"{index}.  {entry['step_id']:<24} complete")
            item.setData(Qt.ItemDataRole.AccessibleTextRole, f"Step {index}, {entry['step_id']}, complete")
            steps.addItem(item)
        layout.addWidget(heading)
        layout.addWidget(steps)
        return panel

    def _trace_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        heading = QLabel("Acceptance trace")
        heading.setObjectName("sectionTitle")
        trace = QPlainTextEdit(json.dumps(self.trace, indent=2))
        trace.setObjectName("trace")
        trace.setAccessibleName("Shared benchmark acceptance trace")
        trace.setReadOnly(True)
        trace.setFont(QFont("Cascadia Mono", 10))
        layout.addWidget(heading)
        layout.addWidget(trace)
        return panel


STYLESHEET = """
QMainWindow, QWidget { background: #0d1420; color: #e8edf5; font: 14px "Segoe UI"; }
QLabel#eyebrow { color: #7dc8ff; font-size: 14px; letter-spacing: 2px; }
QLabel#title { font-size: 30px; font-weight: 700; margin: 12px 0; }
QLabel#sectionTitle { font-size: 22px; font-weight: 700; margin: 8px; }
QFrame#panel { background: #172235; border: 1px solid #30415d; border-radius: 10px; }
QListWidget, QPlainTextEdit { background: #172235; border: 0; color: #dbe6f5; padding: 8px; }
QListWidget::item { padding: 8px; color: #8ed8a7; }
QListWidget::item:selected { background: #29415f; color: #ffffff; }
QSplitter::handle { background: #0d1420; width: 12px; }
QScrollBar:vertical { background: #111a29; width: 12px; }
QScrollBar::handle:vertical { background: #63738a; min-height: 28px; border-radius: 5px; }
"""


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=SPIKE_ROOT / "benchmark-input")
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    fixture = _load(args.input / "fixture.json")
    scenario = _load(args.input / "scenario.json")
    application = QApplication(sys.argv[:1])
    window = BenchmarkWindow(fixture, scenario)
    if args.trace:
        args.trace.parent.mkdir(parents=True, exist_ok=True)
        args.trace.write_text(json.dumps(window.trace, indent=2) + "\n", encoding="utf-8")
    window.show()
    if args.screenshot:
        application.processEvents()
        args.screenshot.parent.mkdir(parents=True, exist_ok=True)
        if not window.grab().save(str(args.screenshot)):
            raise RuntimeError(f"failed to save screenshot: {args.screenshot}")
        window.close()
        return 0
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
