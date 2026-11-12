"""The page the browser shows after a connector sign-in."""

from __future__ import annotations

from aldwin.core.connectors.oauth_page import callback_page


def test_callback_page_uses_the_web_tokens_in_light_and_dark() -> None:
    page = callback_page("ok", "Linear")
    assert "--background: oklch(0.992 0.002 var(--tint))" in page
    assert "@media (prefers-color-scheme: dark)" in page
    assert "--background: oklch(0.17 0.003 var(--tint))" in page
    assert "38bdb0" not in page
    assert "teal" not in page


def test_a_declined_sign_in_points_to_settings_and_the_terminal() -> None:
    page = callback_page("declined", "Linear")
    assert "Settings, under Tools" in page
    assert "<code>/connectors</code>" in page
