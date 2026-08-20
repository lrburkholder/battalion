"""Battalion desktop visual tokens and bundled brand assets."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFontDatabase, QIcon


ASSET_ROOT = Path(__file__).resolve().parent / "assets"
FONT_ROOT = ASSET_ROOT / "fonts"
APPLICATION_ICON = ASSET_ROOT / "favicon.ico"
BRAND_ICON = ASSET_ROOT / "icon-512.png"
SANS_FONT_FAMILY = "IBM Plex Sans"
MONO_FONT_FAMILY = "IBM Plex Mono"
FONT_FILES = (
    "IBMPlexSans-Regular.ttf",
    "IBMPlexSans-Medium.ttf",
    "IBMPlexSans-SemiBold.ttf",
    "IBMPlexMono-Regular.ttf",
    "IBMPlexMono-Medium.ttf",
    "IBMPlexMono-SemiBold.ttf",
)

_fonts_loaded = False


def load_bundled_fonts() -> tuple[str, ...]:
    """Register the bundled IBM Plex families once for this Qt process."""
    global _fonts_loaded
    if _fonts_loaded:
        return (SANS_FONT_FAMILY, MONO_FONT_FAMILY)

    loaded_families: set[str] = set()
    for filename in FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(FONT_ROOT / filename))
        if font_id < 0:
            raise RuntimeError(f"Could not load bundled desktop font: {filename}")
        loaded_families.update(QFontDatabase.applicationFontFamilies(font_id))
    _fonts_loaded = True
    return tuple(sorted(loaded_families))


def application_icon() -> QIcon:
    """Return the shared window/taskbar icon."""
    return QIcon(str(APPLICATION_ICON))


STYLESHEET = """
QMainWindow, QWidget {
    background: #1a1b1e;
    color: #d8d9dc;
    font-family: "IBM Plex Sans";
    font-size: 13px;
}
QMenuBar, QMenu {
    background: #212226;
    color: #d8d9dc;
    border-bottom: 1px solid #2c2d31;
}
QMenu { border: 1px solid #3a3c42; }
QMenu::item { padding: 6px 72px 6px 12px; }
QMenu::item:selected { background: #26282c; }
QLabel#title {
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 1px;
}
QFrame#topBar {
    background: #212226;
    border: 0;
    border-bottom: 1px solid #2c2d31;
}
QLabel#navSeparator { color: #3a3c42; }
QLabel#projectIdentity {
    color: #8b8d93;
    font-family: "IBM Plex Mono";
    font-size: 11px;
}
QLabel#sectionTitle {
    color: #d8d9dc;
    font-family: "IBM Plex Mono";
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 1px;
    padding: 3px 0;
}
QLabel#panelTitle {
    color: #55575c;
    font-family: "IBM Plex Mono";
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 1px;
}
QLabel#viewState, QLabel#liveState {
    color: #8b8d93;
    font-family: "IBM Plex Mono";
    font-size: 11px;
    padding: 3px 1px;
}
QLabel#viewState[state="error"] { color: #d65b5b; }
QFrame#panel, QFrame#actionPanel {
    background: #212226;
    border: 1px solid #2c2d31;
    border-radius: 2px;
}
QListWidget, QTreeWidget, QPlainTextEdit, QLineEdit, QComboBox {
    background: #17181b;
    color: #d8d9dc;
    border: 1px solid #3a3c42;
    border-radius: 2px;
    padding: 5px;
    selection-background-color: #263a58;
    selection-color: #d8d9dc;
}
QPlainTextEdit {
    font-family: "IBM Plex Mono";
    font-size: 12px;
}
QLineEdit:focus, QComboBox:focus, QListWidget:focus,
QTreeWidget:focus, QPlainTextEdit:focus {
    border-color: #5b8dd6;
}
QListWidget::item, QTreeWidget::item {
    min-height: 27px;
    border-left: 2px solid transparent;
}
QListWidget::item:hover, QTreeWidget::item:hover { background: #26282c; }
QListWidget::item:selected, QTreeWidget::item:selected {
    background: #212226;
    border-left-color: #5b8dd6;
}
QHeaderView::section {
    background: #212226;
    color: #55575c;
    font-family: "IBM Plex Mono";
    font-size: 10px;
    font-weight: 500;
    letter-spacing: 1px;
    padding: 7px;
    border: 0;
    border-bottom: 1px solid #2c2d31;
}
QPushButton {
    background: #212226;
    color: #8b8d93;
    border: 1px solid #3a3c42;
    border-radius: 2px;
    padding: 6px 10px;
    font-family: "IBM Plex Mono";
    font-size: 11px;
}
QPushButton:hover { background: #26282c; color: #d8d9dc; }
QPushButton:focus { border-color: #5b8dd6; }
QPushButton:pressed { background: #263a58; }
QPushButton:disabled { color: #55575c; border-color: #2c2d31; }
QComboBox::drop-down { border: 0; width: 20px; }
QSplitter::handle { background: #1a1b1e; width: 7px; }
QScrollBar:vertical {
    background: #17181b;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #3a3c42;
    min-height: 24px;
    border-radius: 2px;
}
QScrollBar::handle:vertical:hover { background: #55575c; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    background: #17181b;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #3a3c42;
    min-width: 24px;
    border-radius: 2px;
}
QScrollBar::handle:horizontal:hover { background: #55575c; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QToolTip {
    background: #212226;
    color: #d8d9dc;
    border: 1px solid #3a3c42;
    padding: 4px;
}
"""
