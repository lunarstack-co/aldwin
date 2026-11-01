"""Answer a `connector.auth` event: open the browser or ask for a token.

Used by `/connectors` and by the chat screen, where a connector Aldwin
proposed signs in during a turn. The secret never goes to the model: a token
is read with a hidden prompt and sent straight to the host.
"""

from __future__ import annotations

import asyncio
import webbrowser
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from aldwin.client import ProtocolError, sanitize_display_text
from aldwin.protocol import ConnectorAuthEvent

PromptLine = Callable[[str], Awaitable[str]]
Line = Callable[[str, str], None]


class AuthClient(Protocol):
    async def stop(self) -> Any: ...

    async def connectors_auth_paste(self, name: str, redirect_url: str) -> None: ...

    async def connectors_auth_token(self, name: str, token: str) -> None: ...


async def answer_connector_auth(
    event: ConnectorAuthEvent,
    *,
    client: AuthClient,
    line: Line,
    gap: Callable[[], None],
    prompt_line: PromptLine,
    prompt_secret: PromptLine,
) -> None:
    """Walk the user through one sign-in prompt. Empty input stops the flow."""
    title = sanitize_display_text(event.title)
    if event.mode == "token":
        await _answer_token(event, title, client, line, gap, prompt_secret)
        return
    url = sanitize_display_text(event.url or "")
    minutes = max(1, event.expires_in // 60)
    gap()
    line(f"Sign in to {title}", "bold")
    # Only web addresses go to the browser; anything else is printed to paste.
    web = (event.url or "").startswith(("https://", "http://"))
    opened = (
        event.mode == "browser"
        and web
        and await asyncio.to_thread(webbrowser.open, event.url or "")
    )
    if opened:
        line(
            "Your browser should open. If it doesn't, open this link "
            f"(it works for {minutes} minutes):",
            "muted",
        )
    else:
        line(f"Open this link in a browser (it works for {minutes} minutes):", "muted")
    line(url, "accent")
    gap()
    if opened:
        line("Waiting for you to finish in the browser…", "muted")
    else:
        line(
            "Sign in, then paste the address the browser ends on "
            "(it starts with http://127.0.0.1).",
            "muted",
        )
    while True:
        try:
            raw = await prompt_line("  Paste the address, or press Enter to cancel: ")
        except (EOFError, KeyboardInterrupt):
            raw = ""
        if not raw.strip():
            await client.stop()
            return
        try:
            await client.connectors_auth_paste(event.name, raw.strip())
            return
        except ProtocolError as exc:
            line(str(exc), "warn")


async def _answer_token(
    event: ConnectorAuthEvent,
    title: str,
    client: AuthClient,
    line: Line,
    gap: Callable[[], None],
    prompt_secret: PromptLine,
) -> None:
    gap()
    line(f"{title} needs a token.", "bold")
    if event.token_help_url:
        line(f"Create one at {sanitize_display_text(event.token_help_url)}", "muted")
    try:
        token = (await prompt_secret("  Token (hidden): ")).strip()
    except (EOFError, KeyboardInterrupt):
        token = ""
    if not token:
        await client.stop()
        return
    await client.connectors_auth_token(event.name, token)
