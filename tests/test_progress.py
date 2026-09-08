"""Tests for battalion.progress — the CLI's live run display (rich)."""
import io
import json

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


def test_role_contract_correction_is_visible_without_claiming_a_write():
    buf = io.StringIO()
    display = ProgressDisplay(stream=buf)

    display.handle_event({"type": "node_start", "node": "driver_green"})
    display.handle_event({
        "type": "role_contract_correction",
        "node": "driver_green",
        "reason_code": "driver-mode-artifact",
        "offending_paths": ["tests/test_widget.py"],
        "mutation_applied": False,
        "attempt_number": 1,
    })

    output = buf.getvalue()
    assert "[caught] Driver (GREEN)" in output
    assert "prohibited output was not written" in output
    assert "Correcting and retrying the same role" in output


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


def test_adjacent_reasoning_fragments_share_one_trace_label():
    display = ProgressDisplay(stream=io.StringIO(), show_stream=True)

    display.handle_token({"type": "reasoning", "content": "We "})
    display.handle_token({"type": "reasoning", "content": "need "})
    display.handle_token({"type": "reasoning", "content": "tests."})

    assert "".join(display._trace) == "[reasoning] We need tests."


def test_trace_retains_early_content_beyond_the_old_rolling_tail_limit():
    display = ProgressDisplay(stream=io.StringIO(), show_stream=True)

    display.handle_token({"type": "token", "content": "begin "})
    display.handle_token({"type": "token", "content": "x" * 1_000})

    assert "".join(display._trace).startswith("begin ")


def test_completed_interactive_node_is_preserved_in_terminal_history():
    class LiveStub:
        def refresh(self):
            pass

    buf = io.StringIO()
    display = ProgressDisplay(stream=buf, show_stream=True)
    display._interactive = True
    display._live = LiveStub()

    display.handle_event({"type": "node_start", "node": "architect"})
    display.handle_token({"type": "reasoning", "content": "Choose the small design."})
    display.handle_event({"type": "node_end", "node": "architect", "phase": "driver"})

    assert "Choose the small design." in buf.getvalue()
    assert display._trace == []


def test_live_panel_bounds_its_view_without_discarding_the_node_trace():
    display = ProgressDisplay(stream=io.StringIO(), show_stream=True)
    display.handle_token({"type": "token", "content": "begin "})
    display.handle_token({"type": "token", "content": "x" * 2_000})

    visible, notice = display._trace_for_live_panel()

    assert notice is not None
    assert visible.endswith("x" * 100)
    assert "".join(display._trace).startswith("begin ")


def test_trace_output_records_node_associated_token_and_reasoning_events():
    trace = io.StringIO()
    display = ProgressDisplay(trace_output=trace, run_ref="run-42")

    display.handle_event({"type": "node_start", "node": "architect"})
    display.handle_token({"type": "reasoning", "content": "consider scope"})
    display.handle_token({"type": "token", "content": "# Plan"})

    events = [json.loads(line) for line in trace.getvalue().splitlines()]
    assert [(event["node"], event["kind"], event["content"]) for event in events] == [
        ("architect", "reasoning", "consider scope"),
        ("architect", "token", "# Plan"),
    ]
    assert [event["sequence"] for event in events] == [1, 2]
    assert {event["run_ref"] for event in events} == {"run-42"}


def test_token_events_skipped_before_any_node_starts():
    buf = io.StringIO()
    display = ProgressDisplay(stream=buf, show_stream=True)

    display.handle_token({"type": "token", "content": "orphan"})

    assert "".join(display._trace) == "orphan"
    assert buf.getvalue() == ""
