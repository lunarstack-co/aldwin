"""Status bar and session header model formatting."""

from __future__ import annotations

from aldwin.cli.chat_screen import (
    format_session_header,
    format_toolbar_model_segment,
    to_plain_text,
)


def test_status_bar_model_formatting() -> None:
    """Status bar formatting shows `{model id} · {provider title}`."""
    seg = format_toolbar_model_segment("ollama/qwen3:8b", 500)
    plain_text = to_plain_text(seg)
    assert "qwen3:8b · Ollama" in plain_text

    seg_openai = format_toolbar_model_segment("gpt-4o", 500)
    plain_openai = to_plain_text(seg_openai)
    assert "gpt-4o · OpenAI" in plain_openai


def test_history_session_header_formatting() -> None:
    """/history header formatting shows `{model id} · {provider title}`."""
    header = format_session_header("anthropic/claude-sonnet-4-5")
    assert header == "claude-sonnet-4-5 · Anthropic"

    header_with_id = format_session_header("openai/gpt-4o", "session-123")
    assert header_with_id == "gpt-4o · OpenAI (session-123)"


def test_welcome_renders_model_provider_title() -> None:
    """render_welcome displays {model id} · {provider title}."""
    import io

    from aldwin.cli.chat_screen import format_model_provider_title
    from aldwin.cli.render import ChatRenderer
    from aldwin.cli.theme import create_console

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    model_title = format_model_provider_title("ollama/qwen3:8b")
    renderer.render_welcome(
        first_run=False,
        model=model_title,
        display_home="/home/user",
    )
    assert "Aldwin · qwen3:8b · Ollama · /home/user" in console.export_text()
