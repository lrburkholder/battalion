"""Live progress rendering for Battalion CLI runs.

A Battalion run drives up to four LLM-driven nodes back to back with no
intermediate output, which is opaque to a human watching a terminal. This
module turns the run's event stream into something watchable: which node is
active, budget usage, interrupt events, and — when the underlying LLM call
streams — the model's tokens/reasoning as they arrive (the "agent traces"
effect seen in tools like OpenCode and VS Code Copilot).

Two modes:
  * Interactive terminal (default): a compact rich.Live region that redraws
    in place — spinner, current node, and budget. Completed nodes are printed
    in full into terminal scrollback: reasoning as text and structured file
    output as syntax-highlighted source, rather than an escaped JSON blob.
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
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
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
        self._reasoning: list[str] = []
        self._content: list[str] = []
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
            self._reasoning = []
            self._content = []
            self._last_trace_kind = None
            self._budget = event.get("budget") or {}
            if not self._interactive:
                self._console.print(f"[run] {self._node_label}...")
        elif etype == "node_end":
            if self._interactive and self._trace:
                self._print_completed_node()
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
            if self._interactive and self._trace:
                # Validation happens after a streamed response has completed.
                # Preserve that response for the operator before reporting the
                # interrupt instead of letting Live erase it on shutdown.
                self._print_completed_node(title_suffix="rejected output")
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
            self._reasoning.append(content)
        else:
            self._trace.append(content)
            self._content.append(content)
        self._last_trace_kind = kind
        self._write_trace_event(kind, content)

    def _print_completed_node(self, title_suffix: str | None = None) -> None:
        """Print a full, static transcript above Live and reset its buffers."""
        self._console.print(self._render_completed_node(title_suffix))
        self._trace = []
        self._reasoning = []
        self._content = []
        self._last_trace_kind = None

    @staticmethod
    def _file_output(content: str) -> dict[str, str] | None:
        """Best-effort extract a Driver/Refactorer ``files`` object.

        Rendering is presentation only: the node remains the authority that
        validates structured output before a write.  This permissive reader
        lets an operator inspect a malformed response as source when possible.
        """
        decoder = json.JSONDecoder()
        for offset, character in enumerate(content):
            if character != "{":
                continue
            try:
                candidate, _ = decoder.raw_decode(content[offset:])
            except json.JSONDecodeError:
                continue
            files = candidate.get("files") if isinstance(candidate, dict) else None
            if (
                isinstance(files, dict)
                and all(isinstance(path, str) and isinstance(text, str)
                        for path, text in files.items())
            ):
                return files
        return None

    def _render_completed_output(self):
        content = "".join(self._content)
        if not content:
            return None
        files = self._file_output(content)
        if files:
            return Group(*[
                Panel(
                    Syntax(text, Syntax.guess_lexer(path, text), word_wrap=True),
                    title=f"Output file: {path}",
                    border_style="green",
                )
                for path, text in files.items()
            ])
        if self._node == "architect":
            return Panel(Markdown(content), title="Output: plan.md", border_style="green")
        return Panel(Text(content), title="Model output", border_style="green")

    def _render_completed_node(self, title_suffix: str | None = None) -> Panel:
        header = self._header()
        parts: list[object] = [header]
        if self._reasoning:
            parts.append(Panel(
                Text("".join(self._reasoning)),
                title="Reasoning (full transcript)",
                border_style="dim",
            ))
        output = self._render_completed_output()
        if output is not None:
            parts.append(output)
        title = "Battalion" if title_suffix is None else f"Battalion — {title_suffix}"
        return Panel(Group(*parts), title=title)

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

    def _header(self):
        header = Text(self._node_label, style="bold cyan")
        if self._budget:
            header.append(
                f"   budget {self._budget.get('used', 0)}/{self._budget.get('limit', 0)}",
                style="dim",
            )
        top = Columns([Spinner("dots"), header], equal=False, expand=False)
        return top

    def _render(self) -> Panel:
        body = Group(
            self._header(),
            Text(
                "streaming; full reasoning and rendered output follow when this node finishes",
                style="dim",
            ) if self._trace else Text("working...", style="dim"),
        )
        return Panel(body, title="Battalion")
