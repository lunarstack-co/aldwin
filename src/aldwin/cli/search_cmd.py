"""`/search`: pick, connect, switch, update and disconnect web search providers."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Final, Protocol

from prompt_toolkit.input import Input
from rich.text import Text

from aldwin.cli import secret_prompt
from aldwin.cli.picker import Row
from aldwin.cli.provider_actions import ask_line, choose, confirm, pick_action
from aldwin.cli.render import ChatRenderer
from aldwin.client import ProtocolError
from aldwin.protocol import (
    DoneEvent,
    ErrorEvent,
    SearchProviderItem,
    SearchProvidersEvent,
)

DOCS_BASE_URL: Final[str] = "https://askaldwin.com/docs/search"
DEFAULT_SEARXNG_URL: Final[str] = "http://127.0.0.1:8080"
_TITLE_WIDTH: Final[int] = 12

USE = "Use for search"
UPDATE_KEY = "Update key"
CHANGE_ADDRESS = "Change address"
DISCONNECT = "Disconnect"


class SearchClient(Protocol):
    """What `/search` needs from the client."""

    def describe_active_search(self) -> str: ...

    async def search_list(self) -> SearchProvidersEvent: ...

    async def search_connect(
        self, provider: str, *, key: str | None = None, url: str | None = None
    ) -> tuple[DoneEvent | ErrorEvent, list[str]]: ...

    async def search_use(self, provider: str) -> DoneEvent | ErrorEvent: ...

    async def search_disconnect(self, provider: str) -> DoneEvent | ErrorEvent: ...


def _source_label(item: SearchProviderItem) -> str:
    if item.source == "env":
        return f"from {item.env_var}"
    if item.source == "file":
        return "saved"
    if item.source == "config":
        return "saved address"
    if item.source == "detected":
        return "found automatically"
    return ""


def _row(item: SearchProviderItem) -> str:
    name = f"{item.title}{' (in use)' if item.active else ''}"
    if item.connected:
        shown = item.detail if item.kind == "local" else f"key {item.detail}"
        extra = " · ".join(x for x in (shown, _source_label(item)) if x)
    else:
        extra = item.check_note or ""
    return f"{name.ljust(_TITLE_WIDTH + 9)}{extra}".rstrip()


def _header(event: SearchProvidersEvent) -> str:
    active = next((i for i in event.items if i.active), None)
    if active is None:
        return "Web search · not set up"
    how = "automatic" if event.setting == "auto" else "chosen"
    return f"Web search · {active.title} ({how})"


class _SearchScreen:
    def __init__(
        self,
        client: SearchClient,
        renderer: ChatRenderer,
        prompt_line: Callable[[str], Awaitable[str]],
        prompt_secret: Callable[[str], Awaitable[str]] | None,
        input_dev: Input | None,
    ) -> None:
        self.client = client
        self.console = renderer.console
        self.renderer = renderer
        self.ask = prompt_line
        # Look the default up late so tests can replace `secret_prompt.prompt_secret`.
        self.ask_secret = prompt_secret or (
            lambda text: secret_prompt.prompt_secret(text)
        )
        self.input_dev = input_dev
        self.use_picker = (
            isinstance(input_dev, Input)
            and self.console.is_interactive
            and not renderer.plain
        )

    def ok(self, message: str) -> None:
        self.console.print(Text(f"✓ {message}", style="ok"))

    def fail(self, message: str) -> None:
        self.console.print(Text(message, style="err"), soft_wrap=True)

    async def overview(self) -> SearchProvidersEvent | None:
        """The provider list, or None (with the reason printed) if the host failed."""
        try:
            return await self.client.search_list()
        except ProtocolError as exc:
            self.fail(str(exc))
            return None

    async def run(self) -> None:
        while True:
            event = await self.overview()
            if event is None:
                return
            rows: list[Row] = []
            targets: list[SearchProviderItem | None] = []
            disabled: set[int] = set()
            for heading, connected in (("Connected", True), ("Not connected", False)):
                group = [i for i in event.items if i.connected is connected]
                if not group:
                    continue
                disabled.add(len(rows))
                rows.append([("class:heading", heading)])
                targets.append(None)
                for item in group:
                    rows.append(_row(item))
                    targets.append(item)
            auto = "Automatic".ljust(_TITLE_WIDTH + 9) + "use the first connected"
            rows.append(f"{auto}{' (current)' if event.setting == 'auto' else ''}")
            targets.append(None)

            default = next(
                (i for i, t in enumerate(targets) if t is not None and t.active),
                len(rows) - 1,
            )
            choice = await choose(
                rows,
                console=self.console,
                ask=self.ask,
                use_picker=self.use_picker,
                input_dev=self.input_dev,
                plain=self.renderer.plain,
                default=default,
                disabled=disabled,
                header=_header(event),
            )
            if choice is None:
                return
            target = targets[choice]
            if target is None:
                await self.use("auto", "automatic")
                return
            if not target.connected:
                if await self.connect(target):
                    return
                continue
            if await self.manage(target, event.setting):
                return

    async def use(self, name: str, title: str) -> bool:
        result = await self.client.search_use(name)
        if isinstance(result, ErrorEvent):
            self.fail(result.message)
            return False
        if name == "auto":
            refreshed = await self.overview()
            items = refreshed.items if refreshed is not None else []
            active = next((i.title for i in items if i.active), None)
            suffix = f" (using {active})" if active else " (nothing connected yet)"
            self.ok(f"Search · automatic{suffix}")
        else:
            self.ok(f"Search · {title}")
        return True

    async def connect(self, item: SearchProviderItem) -> bool:
        """Connect a provider; True once it works, False if the user backed out."""
        if item.kind == "local":
            return await self.connect_searxng(item)
        self.console.print(
            Text(f"Get your {item.title} key at {item.signup_url}", style="muted")
        )
        self.console.print(Text(f"Guide: {DOCS_BASE_URL}/{item.name}", style="muted"))
        if item.check_note:
            self.console.print(Text(f"Note: the {item.check_note}.", style="muted"))
        while True:
            key = (
                await ask_line(
                    self.ask_secret, f"{item.title} API key (Enter to cancel): "
                )
            ).strip()
            if not key:
                self.console.print(Text("Cancelled.", style="muted"))
                return False
            self.console.print(Text("Checking key…", style="muted"))
            result, notices = await self.client.search_connect(item.name, key=key)
            if isinstance(result, DoneEvent):
                self.ok(f"{item.title} connected · now used for search")
                for notice in notices:
                    self.console.print(Text(notice, style="warn"), soft_wrap=True)
                return True
            self.fail(result.message)

    async def connect_searxng(self, item: SearchProviderItem) -> bool:
        current = item.detail or DEFAULT_SEARXNG_URL
        address = (
            await ask_line(self.ask, f"SearXNG address [{current}]: ")
        ).strip() or current
        self.console.print(Text("Checking SearXNG…", style="muted"))
        result, _ = await self.client.search_connect("searxng", url=address)
        if isinstance(result, DoneEvent):
            self.ok(f"SearXNG connected at {address.rstrip('/')} · now used for search")
            return True
        self.fail(result.message)
        return False

    async def manage(self, item: SearchProviderItem, setting: str) -> bool:
        """Actions for a connected provider; True when the screen should close."""
        actions: list[str] = []
        if not (item.active and setting == item.name):
            actions.append(USE)
        actions.append(CHANGE_ADDRESS if item.kind == "local" else UPDATE_KEY)
        if item.source != "detected":
            actions.append(DISCONNECT)
        action = await pick_action(
            item.title,
            actions,
            console=self.console,
            ask=self.ask,
            use_picker=self.use_picker,
            input_dev=self.input_dev,
            plain=self.renderer.plain,
        )
        if action == USE:
            return await self.use(item.name, item.title)
        if action in (UPDATE_KEY, CHANGE_ADDRESS):
            return await self.connect(item)
        if action == DISCONNECT:
            if not await confirm(self.ask, f"Disconnect {item.title}?"):
                return False
            result = await self.client.search_disconnect(item.name)
            if isinstance(result, ErrorEvent):
                self.fail(result.message)
                return False
            self.ok(f"{item.title} disconnected")
            return False
        return False


async def handle_search_command(
    arg: str,
    *,
    client: SearchClient,
    renderer: ChatRenderer,
    prompt_line: Callable[[str], Awaitable[str]],
    prompt_secret: Callable[[str], Awaitable[str]] | None = None,
    input_dev: Input | None = None,
) -> None:
    """Run `/search`, `/search status`, or `/search <provider>`."""
    screen = _SearchScreen(client, renderer, prompt_line, prompt_secret, input_dev)
    wanted = arg.strip().lower()
    if not wanted:
        await screen.run()
        return
    if wanted == "status":
        status = await asyncio.to_thread(client.describe_active_search)
        renderer.console.print(Text(status, style="muted"))
        return
    if wanted == "auto":
        await screen.use("auto", "automatic")
        return
    event = await screen.overview()
    if event is None:
        return
    aliases = {"searx": "searxng"}
    item = next((i for i in event.items if i.name == aliases.get(wanted, wanted)), None)
    if item is None:
        names = ", ".join(["auto", *(i.name for i in event.items), "status"])
        screen.fail(f"Unknown search provider {arg.strip()!r}.")
        renderer.console.print(Text(f"Usage: /search [{names}]", style="muted"))
        return
    if item.connected:
        await screen.use(item.name, item.title)
        return
    await screen.connect(item)
