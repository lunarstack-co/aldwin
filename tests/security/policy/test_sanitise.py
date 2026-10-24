"""display sanitising strips ANSI/control/bidi; writes stay exact."""

from __future__ import annotations

from types import SimpleNamespace

from aldwin.core.policy import sanitize_display_text


def test_sanitize_strips_ansi_control_bidi_and_redacts() -> None:
    """Approval/row display text is cleaned; secrets redacted."""
    dirty = "Edit \x1b[31mnotes\u202e.md\x07 with sk-1234567890abcdefghijklmno"
    assert sanitize_display_text(dirty) == "Edit notes.md with ‹redacted›"


async def test_written_bytes_keep_ansi(
    security_tools: SimpleNamespace,
    set_answer,
) -> None:
    """Approved file bytes keep ANSI/bidi that display sanitising strips."""
    content = "line with \x1b[31mred\x1b[0m and \u202e bidi\n"
    set_answer("yes")
    out = await security_tools.tools["write_file"].call(
        {"path": "keep.txt", "content": content}
    )
    assert out.ok is True
    written = (security_tools.root / "keep.txt").read_text(encoding="utf-8")
    assert written == content
    assert "\x1b[31m" in written
