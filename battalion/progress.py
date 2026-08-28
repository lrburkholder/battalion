"""Live progress rendering for Battalion CLI runs.

A Battalion run drives up to four LLM-driven nodes back to back with no
intermediate output, which is opaque to a human watching a terminal. This
module turns the run's event stream into something watchable: which node is
active, budget usage, interrupt events, and — when the underlying LLM call
streams — the model's tokens/reasoning as they arrive (the "agent traces"
effect seen in tools like OpenCode and VS Code Copilot).

Two modes:
  * Interactive terminal (default): a rich.Live region that redraws in
    place — spinner, current node, budget, and a bounded live tail of the
    current stream. Completed nodes are printed in full into terminal
    scrollback for later reading.
  * Non-interactive (pipes, CI, the click test runner): one plain text line
    per lifecycle event, so output stays readable and assertable.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from battalion.interrupts.triggers import get_trigger_name

_NODE_LABELS = {
    "architect": "Architect - producing plan.md",
    "driver_red": "Driver (RED) - writing failing tests",
    "driver_green": "Driver (GREEN) - writing implementation",
    "reviewer_red": "Reviewer (RED_CHECK) - verifying tests fail",
    "reviewer_green": "Reviewer (GREEN_CHECK) - verifying tests pass",
    "reviewer_refactor": "Reviewer (REFACTOR_CHECK) - verifying tests still pass",
    "refactorer": "Refactorer - cleaning up implementation",
    "done": "Done",
    "awaiting_human": "Awaiting human",
}

class ProgressDisplay:
    """Renders a Battalion run's event stream.

    Wired into run_ticket/resume_ticket as the on_node_event / on_token
    callbacks. Safe to use as a context manager in the CLI: enter starts
    the live region (interactive only), exit stops it.
    """

    def __init__(
        self,
        stream=None,
        show_stream: bool = True,
        trace_output: TextIO | None = None,
        run_ref: str | None = None,
    ) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._show_stream = show_stream
        self._trace_output = trace_output
        self._run_ref = run_ref
        self._trace_sequence = 0
        self._interactive = bool(getattr(self._stream, "isatty", lambda: False)())
        self._console = Console(
            file=self._stream,
            highlight=False,
            markup=False,
            force_terminal=self._interactive,
        )
        self._live: Live | None = None
        self._node: str | None = None
        self._node_label = ""
        self._trace: list[str] = []
        self._last_trace_kind: str | None = None
        self._budget: dict[str, Any] = {}

    def __enter__(self) -> "ProgressDisplay":
        if self._interactive:
            self._live = Live(
                console=self._console,
                refresh_per_second=10,
                vertical_overflow="ellipsis",
                get_renderable=self._render,
            )
            self._live.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._live is not None:
            self._live.__exit__(exc_type, exc, tb)
            self._live = None

    def handle_event(self, event: dict) -> None:
        """Handle a node lifecycle event (on_node_event callback)."""
        etype = event.get("type")
        node = event.get("node")
        if etype == "node_start":
            self._node = node if isinstance(node, str) else None
            self._node_label = _NODE_LABELS.get(node or "", node or "")
            self._trace = []
            self._last_trace_kind = None
            self._budget = event.get("budget") or {}
            if not self._interactive:
                self._console.print(f"[run] {self._node_label}...")
        elif etype == "node_end":
            if self._interactive and self._trace:
                # Console.print is routed above the active Live region, so the
                # completed node remains in terminal scrollback while the next
                # node gets its own live panel.
                self._console.print(self._render())
                self._trace = []
                self._last_trace_kind = None
            elif not self._interactive:
                self._console.print(
                    f"[run] {self._node_label} -> {event.get('phase')}"
                )
            self._node = None
        elif etype == "interrupt":
            trigger = get_trigger_name(event.get("trigger", ""))
            if not self._interactive:
                self._console.print(f"[pause] {trigger} - awaiting human")
        elif etype == "node_error":
            if not self._interactive:
                self._console.print(
                    f"[error] {self._node_label}: {event.get('error')}"
                )
        if self._live is not None:
            self._live.refresh()

    def handle_token(self, event: dict) -> None:
        """Handle a streamed LLM token event (on_token callback)."""
        if not self._show_stream:
            return
        kind = event.get("type")
        content = event.get("content") or ""
        if kind == "reasoning":
            if self._last_trace_kind != "reasoning":
                self._trace.append("[reasoning] ")
            self._trace.append(content)
        else:
            self._trace.append(content)
        self._last_trace_kind = kind
        self._write_trace_event(kind, content)

    def _trace_for_live_panel(self) -> tuple[str, str | None]:
        """Return a screen-sized tail while retaining the full node trace.

        Rich redraws a Live panel in place. Letting that panel grow beyond the
        terminal height causes visible erase/repaint flashing on Windows.
        The full trace remains in memory until node completion, when it is
        emitted as a static scrollback panel.
        """
        trace = "".join(self._trace)
        width = max(self._console.width, 40)
        limit = min(1_600, max(900, width * 10))
        if len(trace) <= limit:
            return trace, None
        omitted = len(trace) - limit
        return (
            trace[-limit:],
            f"[live tail: {omitted} earlier characters retained for node completion]",
        )

    def _write_trace_event(self, kind: object, content: str) -> None:
        """Append opt-in raw stream text as operator-owned JSON Lines output."""
        if self._trace_output is None:
            return
        self._trace_sequence += 1
        event_kind = "reasoning" if kind == "reasoning" else "token"
        json.dump(
            {
                "schema_version": 1,
                "run_ref": self._run_ref,
                "sequence": self._trace_sequence,
                "occurred_at": datetime.now(timezone.utc).isoformat(),
                "node": self._node,
                "kind": event_kind,
                "content": content,
            },
            self._trace_output,
            ensure_ascii=False,
        )
        self._trace_output.write("\n")
        self._trace_output.flush()

    def _render(self) -> Panel:
        header = Text(self._node_label, style="bold cyan")
        if self._budget:
            header.append(
                f"   budget {self._budget.get('used', 0)}/{self._budget.get('limit', 0)}",
                style="dim",
            )
        top = Columns([Spinner("dots"), header], equal=False, expand=False)
        if self._trace:
            trace, notice = self._trace_for_live_panel()
            body = (
                Group(top, Text(notice, style="dim"), Text(trace))
                if notice is not None
                else Group(top, Text(trace))
            )
        else:
            body = Group(top, Text("working...", style="dim"))
        return Panel(body, title="Battalion")
