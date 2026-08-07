"""Tests for battalion.progress — the CLI's live run display (rich)."""
import io

from battalion.progress import ProgressDisplay


def test_non_interactive_prints_node_start_and_end_lines():
    buf = io.StringIO()
    display = ProgressDisplay(stream=buf)

    display.handle_event({
        "type": "node_start",
        "node": "architect",
        "budget": {"used": 1, "limit": 100},
    })
    display.handle_event({"type": "node_end", "node": "architect", "phase": "driver"})

    out = buf.getvalue()
    assert "[run] Architect - producing plan.md..." in out
    assert "-> driver" in out


def test_non_interactive_interrupt_line_shows_trigger_name():
    buf = io.StringIO()
    display = ProgressDisplay(stream=buf)

    display.handle_event({"type": "node_start", "node": "driver_red"})
    display.handle_event({
        "type": "interrupt", "node": "driver_red", "trigger": "infra-failure",
    })

    assert "[pause] #5: Infra failure - awaiting human" in buf.getvalue()


def test_node_error_line_printed_in_non_interactive_mode():
    buf = io.StringIO()
    display = ProgressDisplay(stream=buf)

    display.handle_event({"type": "node_start", "node": "architect"})
    display.handle_event({
        "type": "node_error", "node": "architect", "error": "boom",
    })

    assert "[error] Architect - producing plan.md: boom" in buf.getvalue()


def test_token_events_suppressed_when_show_stream_false():
    buf = io.StringIO()
    display = ProgressDisplay(stream=buf, show_stream=False)

    display.handle_token({"type": "reasoning", "content": "think"})
    display.handle_token({"type": "token", "content": "plan"})

    assert buf.getvalue() == ""


def test_token_events_accumulate_in_trace_with_reasoning_marked():
    buf = io.StringIO()
    display = ProgressDisplay(stream=buf, show_stream=True)

    display.handle_event({"type": "node_start", "node": "architect"})
    display.handle_token({"type": "reasoning", "content": "think"})
    display.handle_token({"type": "token", "content": "plan"})
    display.handle_token({"type": "token", "content": "text"})

    assert "".join(display._trace) == "[reasoning] thinkplantext"
    # Non-interactive mode never echoes tokens line-by-line — only the
    # node_start line from handle_event is in the buffer.
    out = buf.getvalue()
    assert "think" not in out
    assert "plantext" not in out
    assert "[run] Architect - producing plan.md..." in out


def test_token_events_skipped_before_any_node_starts():
    buf = io.StringIO()
    display = ProgressDisplay(stream=buf, show_stream=True)

    display.handle_token({"type": "token", "content": "orphan"})

    assert "".join(display._trace) == "orphan"
    assert buf.getvalue() == ""
