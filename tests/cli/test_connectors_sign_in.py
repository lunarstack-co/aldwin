"""`/connectors`: the catalogue picker, browser sign-in, paste, token and sign-out."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

import pytest

from aldwin.cli import connectors_view
from aldwin.cli.connectors_view import handle_connectors_command
from aldwin.client import ProtocolError
from aldwin.protocol import (
    CatalogItem,
    ConnectorAuthEvent,
    ConnectorsEvent,
    ConnectorStatusEvent,
    DoneEvent,
    ErrorEvent,
    Event,
)

from .test_connectors_view import FakeConnectorsClient, _info, _renderer

AUTH_URL = "https://auth.notion.example/authorize?state=s1"
NOTION = CatalogItem(
    name="notion",
    title="Notion",
    purpose="Notes and docs",
    url="https://mcp.notion.example/mcp",
    auth="oauth",
    docs_url="https://notion.example/docs",
)
GITHUB = CatalogItem(
    name="github",
    title="GitHub",
    purpose="Code and issues",
    url="https://api.github.example/mcp",
    auth="bearer",
    docs_url="https://github.example/docs",
    token_help_url="https://github.example/settings/tokens",
)
CONTEXT7 = CatalogItem(
    name="context7",
    title="Context7",
    purpose="Library docs",
    url="https://mcp.context7.example/mcp",
    auth="none",
    docs_url="https://context7.example",
)


class FakeSignInClient(FakeConnectorsClient):
    """A host that asks the user once, then waits for the answer."""

    def __init__(self, *, catalog: list[CatalogItem], **kw: Any) -> None:
        super().__init__(**kw)
        self.catalog = catalog
        self.browser: list[str] | None = None  # set: returns once opened
        self.bad_pastes = 0
        self._answered: asyncio.Future[str] | None = None

    async def connectors_list(self) -> ConnectorsEvent:
        return ConnectorsEvent(re="r", items=self.items, catalog=self.catalog)

    async def connectors_sign_in(self, **kw: Any) -> AsyncGenerator[Event, None]:
        self.calls.append(("sign_in", kw))
        self._answered = asyncio.get_running_loop().create_future()
        name = kw["name"] or "custom"
        title = name.capitalize() if name != "github" else "GitHub"
        mode = (
            "token"
            if kw.get("auth") == "bearer"
            else ("paste" if kw["no_browser"] else "browser")
        )
        yield ConnectorAuthEvent(
            re="r",
            name=name,
            title=title,
            mode=mode,
            url=None if mode == "token" else AUTH_URL,
            token_help_url=GITHUB.token_help_url if mode == "token" else None,
            expires_in=300,
        )
        if self.browser is not None:
            async with asyncio.timeout(2):
                while not self.browser:
                    await asyncio.sleep(0.01)
            self._answered.set_result("browser")
        answer = await self._answered
        if answer == "stop":
            yield DoneEvent(re="r", reason="stopped")
            return
        yield ConnectorStatusEvent(re="r", name=name, status="connected", tools=3)
        yield DoneEvent(re="r", reason="ok")

    async def connectors_auth_paste(self, name: str, redirect_url: str) -> None:
        self.calls.append(("paste", name, redirect_url))
        if self.bad_pastes:
            self.bad_pastes -= 1
            raise ProtocolError("That address has no sign-in code.", code="connector")
        assert self._answered is not None
        self._answered.set_result("paste")

    async def connectors_auth_token(self, name: str, token: str) -> None:
        self.calls.append(("token", name, token))
        assert self._answered is not None
        self._answered.set_result("token")

    async def connectors_logout(self, name: str) -> ConnectorStatusEvent:
        self.calls.append(("logout", name))
        return ConnectorStatusEvent(re="r", name=name, status="needs_auth")

    async def stop(self) -> DoneEvent | ErrorEvent:
        self.calls.append(("stop",))
        if self._answered is not None and not self._answered.done():
            self._answered.set_result("stop")
        return DoneEvent(re="r", reason="ok")


def _lines(*answers: str) -> Callable[[str], Awaitable[str]]:
    """Scripted answers; once they run out the prompt waits like a person."""
    queue = list(answers)

    async def prompt(_label: str) -> str:
        if queue:
            return queue.pop(0)
        await asyncio.Event().wait()
        return ""

    return prompt


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []

    def fake_open(url: str) -> bool:
        urls.append(url)
        return True

    monkeypatch.setattr(connectors_view.webbrowser, "open", fake_open)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    return urls


async def _run(client: FakeSignInClient, *answers: str, secret: str = "") -> str:
    renderer, buf = _renderer()

    async def secret_prompt(_label: str) -> str:
        return secret

    await asyncio.wait_for(
        handle_connectors_command(
            client=client,
            renderer=renderer,
            prompt_line=_lines(*answers),
            prompt_secret=secret_prompt,
        ),
        timeout=5,
    )
    return buf.getvalue()


async def test_catalogue_lists_apps_with_tags_and_custom_last() -> None:
    added = GITHUB.model_copy(update={"added": True})
    client = FakeSignInClient(catalog=[NOTION, added, CONTEXT7])
    out = await _run(client, "1", "")
    assert "1. Notion  Notes and docs  [sign in]" in out
    assert "2. GitHub  Code and issues  [✓ added]" in out
    assert "3. Context7  Library docs\n" in out
    assert "4. Custom URL…" in out


async def test_browser_sign_in_from_the_catalogue(opened: list[str]) -> None:
    client = FakeSignInClient(catalog=[NOTION])
    client.browser = opened
    out = await _run(client, "1", "1")

    assert opened == [AUTH_URL]
    assert AUTH_URL in out  # printed too, in case the browser opened elsewhere
    assert "This link works for 5 minutes" in out
    assert "Notion connected. 3 tools are available now." in out
    _kind, kw = client.calls[0]
    assert kw == {
        "name": "notion",
        "url": NOTION.url,
        "auth": "oauth",
        "no_browser": False,
    }


async def test_over_ssh_the_address_is_pasted(
    opened: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`TC-06` (door half): a bad paste is refused and the prompt comes back."""
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.1 22 10.0.0.2 51000")
    client = FakeSignInClient(catalog=[NOTION])
    client.bad_pastes = 1
    back = "http://127.0.0.1:47823/callback?code=c&state=s1"
    out = await _run(client, "1", "1", "nonsense", back)

    assert opened == []
    assert client.calls[0][1]["no_browser"] is True
    assert "paste the address the browser ends on" in out
    assert "That address has no sign-in code." in out
    assert ("paste", "notion", back) in client.calls
    assert "Notion connected." in out


async def test_browser_that_cannot_open_falls_back_to_paste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connectors_view.webbrowser, "open", lambda _url: False)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    client = FakeSignInClient(catalog=[NOTION])
    out = await _run(client, "1", "1", "http://127.0.0.1:47823/callback?code=c")
    assert "paste the address the browser ends on" in out
    assert "Notion connected." in out


async def test_cancel_stops_the_sign_in(opened: list[str]) -> None:
    client = FakeSignInClient(catalog=[NOTION])
    out = await _run(client, "1", "1", "")
    assert ("stop",) in client.calls
    assert "Sign-in cancelled." in out
    assert "Run /connectors to try again." in out


async def test_github_asks_for_a_token_with_help() -> None:
    """`TC-52` (door half)."""
    client = FakeSignInClient(catalog=[GITHUB])
    out = await _run(client, "1", "1", secret="canary-9c1d")
    assert "GitHub needs a token." in out
    assert "Create one at https://github.example/settings/tokens" in out
    assert ("token", "github", "canary-9c1d") in client.calls
    assert "canary-9c1d" not in out


async def test_empty_token_cancels() -> None:
    client = FakeSignInClient(catalog=[GITHUB])
    out = await _run(client, "1", "1", secret="")
    assert ("stop",) in client.calls
    assert "Sign-in cancelled." in out


async def test_catalogue_app_without_sign_in_adds_directly() -> None:
    client = FakeSignInClient(catalog=[CONTEXT7])
    out = await _run(client, "1", "1")
    assert client.calls == [("add", {"url": CONTEXT7.url, "name": "context7"})]
    assert "Context7 connected. 2 tools are available now." in out


async def test_custom_url_that_needs_sign_in_offers_the_browser(
    opened: list[str],
) -> None:
    client = FakeSignInClient(catalog=[NOTION])
    client.needs_token = True
    client.browser = opened
    out = await _run(client, "1", "2", "https://custom.example/mcp", "1")
    _kind, kw = client.calls[-1]
    assert (kw["name"], kw["url"], kw["auth"]) == (
        None,
        "https://custom.example/mcp",
        "oauth",
    )
    assert "Custom connected." in out


async def test_needs_sign_in_row_and_sign_in_again(opened: list[str]) -> None:
    client = FakeSignInClient(
        catalog=[],
        items=[_info(name="notion", title="Notion", auth="oauth", status="needs_auth")],
    )
    client.browser = opened
    out = await _run(client, "1", "1")
    assert "! Notion  needs sign-in" in out
    _kind, kw = client.calls[0]
    assert (kw["name"], kw["url"]) == ("notion", None)
    assert "Notion connected." in out


async def test_sign_out_is_offered_for_browser_sign_in() -> None:
    client = FakeSignInClient(
        catalog=[], items=[_info(name="notion", title="Notion", auth="oauth")]
    )
    out = await _run(client, "1", "6")
    assert "6. Sign out" in out
    assert ("logout", "notion") in client.calls
    assert "Notion signed out. Sign in again from /connectors." in out


async def test_sign_out_is_not_offered_for_token_connectors() -> None:
    client = FakeSignInClient(catalog=[], items=[_info(auth="bearer")])
    out = await _run(client, "1", "")
    assert "Sign out" not in out
