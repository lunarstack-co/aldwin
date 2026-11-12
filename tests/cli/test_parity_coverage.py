"""Extra unit coverage for dock keys, completions chrome, and markdown."""

from __future__ import annotations

from io import StringIO

from prompt_toolkit.formatted_text import to_plain_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from aldwin.cli.completions_menu import format_completion_row
from aldwin.cli.dock import AldwinPromptSession
from aldwin.cli.markdown import render_markdown
from aldwin.cli.paste import PasteStore
from aldwin.cli.theme import create_console, set_terminal_title


def test_format_completion_row_selected_and_plain() -> None:
    selected = format_completion_row(
        selected=True, name="/help", description="Show help"
    )
    assert any("help" in part[1] for part in selected)
    plain = format_completion_row(
        selected=False, name="/exit", description="Quit", plain=True
    )
    assert plain[0][1].startswith("  /exit") or plain[0][1].startswith("> /exit")


def test_markdown_heading_and_code_render() -> None:
    buf = StringIO()
    console = create_console(plain=False, width=60, file=buf)
    console.print(render_markdown("# Title\n\n```py\nx=1\n```\n\n> quoted"))
    text = buf.getvalue()
    assert "Title" in text
    assert "│" in text or "quoted" in text
    assert str(render_markdown("   ")) == ""


def test_dock_busy_and_contextual_footers() -> None:
    with create_pipe_input() as pipe:
        session = AldwinPromptSession(
            input=pipe,
            output=DummyOutput(),
            paste_store=PasteStore(),
            footer_right=lambda: "model · bar",
        )
        session.attach_key_bindings()
        session.set_busy(True)
        busy = to_plain_text(session._toolbar_text())
        assert "esc to interrupt" in busy
        assert "model" in busy
        session.set_busy(False)
        session.set_footer("interrupt")
        assert "esc to interrupt" in to_plain_text(session._toolbar_text())
        session.set_footer("paste", left="paste again to expand")
        assert "paste again" in to_plain_text(session._toolbar_text())
        session.set_footer("shortcuts")
        shortcuts = to_plain_text(session._toolbar_text())
        assert "send" in shortcuts or "↵" in shortcuts
        assert "⌃G" in shortcuts
        session.set_footer("ctrl_c", left="Press Ctrl-C again to exit")
        assert "Ctrl-C again" in to_plain_text(session._toolbar_text())
        session.set_footer("idle")
        assert "? for shortcuts" in to_plain_text(session._toolbar_text())
        session.set_footer("ctrl_c")
        assert "Ctrl-C again" in to_plain_text(session._toolbar_text())
        session.set_footer("paste")
        assert "paste again" in to_plain_text(session._toolbar_text())
        session2 = AldwinPromptSession(input=pipe, output=DummyOutput())
        assert to_plain_text(session2._toolbar_text()).strip().startswith("?")


def test_set_terminal_title_strips_controls() -> None:
    class Buf:
        def __init__(self) -> None:
            self.data = ""

        def write(self, s: str) -> int:
            self.data += s
            return len(s)

        def flush(self) -> None:
            pass

    buf = Buf()
    set_terminal_title(buf, "Hi\x1b]evil\x07", plain=False)  # type: ignore[arg-type]
    assert "\x1b]0;" in buf.data
    assert "evil" in buf.data or "Hi" in buf.data
    assert "\x1b]evil" not in buf.data
