"""`/connectors`: list, add and manage connectors."""

from __future__ import annotations

import asyncio
import contextlib
import os
import webbrowser
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from typing import Literal, Protocol

from prompt_toolkit.input import Input
from rich.text import Text

from aldwin.cli.picker import Row, row_text
from aldwin.cli.render import ChatRenderer
from aldwin.client import ProtocolError, sanitize_display_text
from aldwin.protocol import (
    CatalogItem,
    ConnectorAuth,
    ConnectorAuthEvent,
    ConnectorInfo,
    ConnectorProbeEvent,
    ConnectorsEvent,
    ConnectorStatusEvent,
    ConnectorToolInfo,
    DoneEvent,
    ErrorEvent,
    Event,
    NoticeEvent,
)

PromptLine = Callable[[str], Awaitable[str]]
ToolChoice = Literal["auto", "ask", "off", "default"]

ADD_LABEL = "+ Add a connector…"
CUSTOM_LABEL = "Custom URL…"
_GLYPHS = {
    "connected": ("●", "ok"),
    "failed": ("✕", "err"),
    "disabled": ("○", "muted"),
    "connecting": ("…", "accent"),
    "needs_auth": ("!", "warn"),
}
_AUTH_TAGS = {
    "oauth": ("sign in", "accent"),
    "bearer": ("token", "accent"),
    "none": ("no sign-in", "muted"),
}
_TAG_WIDTH = 10
Outcome = Literal["ok", "warn", "err"]
_OUTCOME_GLYPHS: dict[Outcome, str] = {"ok": "✓", "warn": "!", "err": "✗"}


class ConnectorsClient(Protocol):
    """The client methods this view uses (satisfied by `AldwinClient`)."""

    async def connectors_list(self) -> ConnectorsEvent: ...

    async def connectors_add(
        self,
        *,
        url: str,
        name: str | None = None,
        title: str | None = None,
        auth: ConnectorAuth = "none",
        token: str | None = None,
        token_env: str | None = None,
        dry_run: bool = False,
    ) -> ConnectorProbeEvent | ConnectorStatusEvent: ...

    def connectors_sign_in(
        self,
        *,
        name: str | None,
        url: str | None = None,
        title: str | None = None,
        auth: ConnectorAuth = "oauth",
        client_id: str | None = None,
        no_browser: bool = False,
    ) -> AsyncGenerator[Event, None]: ...

    async def connectors_logout(self, name: str) -> ConnectorStatusEvent: ...

    async def connectors_auth_paste(self, name: str, redirect_url: str) -> None: ...

    async def connectors_auth_token(self, name: str, token: str) -> None: ...

    async def stop(self) -> DoneEvent | ErrorEvent: ...

    async def connectors_enable(self, name: str) -> ConnectorStatusEvent: ...

    async def connectors_disable(self, name: str) -> ConnectorStatusEvent: ...

    async def connectors_remove(self, name: str) -> None: ...

    async def connectors_reconnect(self, name: str) -> None: ...

    async def connectors_tool_policy(
        self, name: str, tool: str, setting: ToolChoice
    ) -> ConnectorsEvent: ...

    async def connectors_trust_hints(
        self, name: str, trust: bool
    ) -> ConnectorsEvent: ...


async def handle_connectors_command(
    *,
    client: ConnectorsClient,
    renderer: ChatRenderer,
    prompt_line: PromptLine,
    prompt_secret: PromptLine,
    input_dev: Input | None = None,
) -> None:
    """Show the connector list and run whatever the user picks."""
    view = _View(client, renderer, prompt_line, prompt_secret, input_dev)
    try:
        await view.run()
    except ProtocolError as exc:
        renderer.console.print(Text(str(exc), style="err"))


class _View:
    def __init__(
        self,
        client: ConnectorsClient,
        renderer: ChatRenderer,
        prompt_line: PromptLine,
        prompt_secret: PromptLine,
        input_dev: Input | None,
    ) -> None:
        self.client = client
        self.renderer = renderer
        self.console = renderer.console
        self.prompt_line = prompt_line
        self.prompt_secret = prompt_secret
        self.input_dev = input_dev

    async def run(self) -> None:
        listing = await self.client.connectors_list()
        if listing.error:
            self._gap()
            self._line(sanitize_display_text(listing.error), "warn")
        width = max((len(i.title) for i in listing.items), default=0)
        rows: list[Row] = [connector_row(info, width) for info in listing.items]
        rows.append([("class:accent", ADD_LABEL)])
        choice = await self._choose(rows, title="Connectors")
        if choice is None:
            return
        if choice == len(listing.items):
            await self.add(listing.catalog)
        else:
            await self.manage(listing.items[choice])

    # --- add ----------------------------------------------------------------

    async def add(self, catalog: Sequence[CatalogItem] = ()) -> None:
        if catalog:
            rows = catalog_rows(catalog, self.console.width)
            choice = await self._choose(rows, title="Add a connector")
            if choice is None:
                return
            if choice < len(catalog):
                await self.add_from_catalog(catalog[choice])
                return
        await self.add_custom()

    async def add_from_catalog(self, item: CatalogItem) -> None:
        title = sanitize_display_text(item.title)
        if item.added:
            self._result("ok", f"{title} is already added", "Pick it from /connectors.")
            return
        if item.auth == "none":
            self._line(f"Connecting {title}…", "muted")
            try:
                status = await self.client.connectors_add(url=item.url, name=item.name)
            except ProtocolError as exc:
                self._result("err", f"{title} didn't connect", str(exc))
                return
            assert isinstance(status, ConnectorStatusEvent)
            self._status_result(title, status)
            return
        await self.sign_in(name=item.name, url=item.url, auth=item.auth)

    async def add_custom(self) -> None:
        url = (await self.prompt_line("  Server URL: ")).strip()
        if not url:
            return
        auth: ConnectorAuth = "none"
        token: str | None = None
        while True:
            try:
                probe = await self.client.connectors_add(
                    url=url, auth=auth, token=token, dry_run=True
                )
                break
            except ProtocolError as exc:
                if exc.code != "auth_required" or token is not None:
                    self._result("err", "Couldn't add that server", str(exc))
                    return
                self._line(str(exc), "warn")
                how = await self._choose(
                    ["Sign in with your browser", "Use a token", "Cancel"],
                    title="How do you sign in?",
                )
                if how == 0:
                    await self.sign_in(name=None, url=url)
                    return
                if how != 1:
                    return
                token = (await self.prompt_secret("  Token (hidden): ")).strip()
                if not token:
                    return
                auth = "bearer"
        assert isinstance(probe, ConnectorProbeEvent)

        typed = (await self.prompt_line(f"  Name [{probe.name}]: ")).strip()
        name = typed or probe.name
        title = probe.title if name == probe.name else name[:1].upper() + name[1:]
        self._gap()
        self._line(probe_summary(title, probe), "fg")
        if await self._choose([f"Add {title}", "Cancel"], title=f"Add {title}?") != 0:
            self._result("warn", "Nothing added")
            return

        try:
            status = await self.client.connectors_add(
                url=url, name=name, auth=auth, token=token
            )
        except ProtocolError as exc:
            self._result("err", f"{title} didn't connect", str(exc))
            return
        assert isinstance(status, ConnectorStatusEvent)
        self._status_result(title, status)

    # --- sign-in ------------------------------------------------------------

    async def sign_in(
        self,
        *,
        name: str | None,
        url: str | None = None,
        auth: ConnectorAuth = "oauth",
    ) -> None:
        """Run a sign-in (or token) flow, answering each `connector.auth` event."""
        no_browser = _cannot_open_browser()
        answering: asyncio.Task[None] | None = None
        status: ConnectorStatusEvent | None = None
        title = name or "The connector"
        signed_in = False
        try:
            async for event in self.client.connectors_sign_in(
                name=name, url=url, auth=auth, no_browser=no_browser
            ):
                if isinstance(event, ConnectorAuthEvent):
                    title = sanitize_display_text(event.title)
                    signed_in = event.mode != "token"
                    await _cancel(answering)
                    answering = asyncio.create_task(self._answer(event))
                elif isinstance(event, ConnectorStatusEvent):
                    status = event
                elif isinstance(event, NoticeEvent):
                    self._line(sanitize_display_text(event.message), "muted")
                elif isinstance(event, ErrorEvent):
                    # Close the paste prompt first, or it redraws under the result.
                    await _cancel(answering)
                    self._result(
                        "err",
                        f"{title} didn't connect",
                        sanitize_display_text(event.message),
                    )
                elif isinstance(event, DoneEvent):
                    await _cancel(answering)
                    if event.reason == "stopped":
                        self._result(
                            "warn",
                            "Sign-in cancelled.",
                            "Run /connectors to try again.",
                        )
                    elif status is not None:
                        self._status_result(title, status, signed_in=signed_in)
        finally:
            await _cancel(answering)

    async def _answer(self, event: ConnectorAuthEvent) -> None:
        title = sanitize_display_text(event.title)
        if event.mode == "token":
            await self._answer_token(event, title)
            return
        url = sanitize_display_text(event.url or "")
        minutes = max(1, event.expires_in // 60)
        self._gap()
        self._line(f"Sign in to {title}", "bold")
        opened = event.mode == "browser" and await asyncio.to_thread(
            webbrowser.open, event.url or ""
        )
        if opened:
            self._line(
                "Your browser should open. If it doesn't, open this link "
                f"(it works for {minutes} minutes):",
                "muted",
            )
        else:
            self._line(
                f"Open this link in a browser (it works for {minutes} minutes):",
                "muted",
            )
        self._line(url, "accent")
        self._gap()
        if opened:
            self._line("Waiting for you to finish in the browser…", "muted")
        else:
            self._line(
                "Sign in, then paste the address the browser ends on "
                "(it starts with http://127.0.0.1).",
                "muted",
            )
        while True:
            try:
                raw = await self.prompt_line(
                    "  Paste the address, or press Enter to cancel: "
                )
            except (EOFError, KeyboardInterrupt):
                raw = ""
            if not raw.strip():
                await self.client.stop()
                return
            try:
                await self.client.connectors_auth_paste(event.name, raw.strip())
                return
            except ProtocolError as exc:
                self._line(str(exc), "warn")

    async def _answer_token(self, event: ConnectorAuthEvent, title: str) -> None:
        self._gap()
        self._line(f"{title} needs a token.", "bold")
        if event.token_help_url:
            help_url = sanitize_display_text(event.token_help_url)
            self._line(f"Create one at {help_url}", "muted")
        try:
            token = (await self.prompt_secret("  Token (hidden): ")).strip()
        except (EOFError, KeyboardInterrupt):
            token = ""
        if not token:
            await self.client.stop()
            return
        await self.client.connectors_auth_token(event.name, token)

    # --- manage -------------------------------------------------------------

    async def manage(self, info: ConnectorInfo) -> None:
        title = sanitize_display_text(info.title)
        if info.auth == "oauth" and info.status == "needs_auth":
            choice = await self._choose(["Sign in", "Remove"], title=title)
            if choice == 0:
                await self.sign_in(name=info.name)
            elif choice == 1:
                await self._remove(info)
            return
        trust = "on" if info.trust_read_only_hints else "off"
        actions = [
            "Tools",
            "Reconnect",
            "Enable" if not info.enabled else "Disable",
            "Remove",
            f"Trust read-only hints: {trust}",
        ]
        if info.auth == "oauth":
            actions.append("Sign out")
        choice = await self._choose(actions, title=title)
        if choice == 0:
            await self.tools(info)
        elif choice == 1:
            await self.client.connectors_reconnect(info.name)
            self._result("ok", f"Reconnecting {title}…")
        elif choice == 2 and info.enabled:
            await self.client.connectors_disable(info.name)
            self._result(
                "ok", f"{title} disabled.", "Its tools are off until you enable it."
            )
        elif choice == 2:
            status = await self.client.connectors_enable(info.name)
            self._status_result(title, status)
        elif choice == 3:
            await self._remove(info)
        elif choice == 4:
            await self.client.connectors_trust_hints(
                info.name, not info.trust_read_only_hints
            )
            now = "off" if info.trust_read_only_hints else "on"
            self._result("ok", f"Trust read-only hints: {now}.")
        elif choice == 5:
            await self.client.connectors_logout(info.name)
            self._result(
                "ok",
                f"{title} signed out.",
                "Its tools stay listed and ask you to sign in again from /connectors.",
            )

    async def _remove(self, info: ConnectorInfo) -> None:
        title = sanitize_display_text(info.title)
        choice = await self._choose(
            ["No", "Yes"], title=f"Remove {title} and its saved sign-in?"
        )
        if choice == 1:
            await self.client.connectors_remove(info.name)
            self._result("ok", f"{title} removed.")

    async def tools(self, info: ConnectorInfo) -> None:
        title = sanitize_display_text(info.title)
        if not info.tools:
            self._result("warn", f"{title} has no tools listed yet.")
            return
        width = max(len(t.name) for t in info.tools)
        choice = await self._choose(
            [tool_row(t, width) for t in info.tools], title=f"{title} tools"
        )
        if choice is None:
            return
        tool = info.tools[choice]
        settings: list[ToolChoice] = ["auto", "ask", "off", "default"]
        picked = await self._choose(
            ["auto (run without asking)", "ask", "off (hide from Aldwin)", "default"],
            title=sanitize_display_text(tool.name),
        )
        if picked is None:
            return
        await self.client.connectors_tool_policy(info.name, tool.name, settings[picked])
        self._result("ok", f"{sanitize_display_text(tool.name)}: {settings[picked]}.")

    # --- helpers ------------------------------------------------------------

    async def _choose(self, rows: Sequence[Row], *, title: str) -> int | None:
        """Arrow-key picker on a terminal, otherwise a numbered list.

        The picker is erased once used; one line keeps what was chosen.
        """
        from aldwin.cli.picker import pick_index

        interactive = (
            isinstance(self.input_dev, Input)
            and self.console.is_interactive
            and not self.renderer.plain
        )
        if interactive:
            choice = await pick_index(
                rows,
                input_dev=self.input_dev,
                plain=self.renderer.plain,
                header=f"  {title}",
                erase_when_done=True,
            )
            if choice is not None:
                picked = row_text(rows[choice]).split("  ")[0].strip()
                self._line(f"{title} › {picked}", "muted")
            return choice
        self._gap()
        self._line(title, "bold")
        for idx, row in enumerate(rows, start=1):
            self.console.print(Text(f"  {idx}. {row_text(row)}".rstrip()))
        raw = (await self.prompt_line("Choose a number (Enter to cancel): ")).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(rows):
            return int(raw) - 1
        return None

    def _gap(self) -> None:
        self.console.print()

    def _line(self, text: str, style: str) -> None:
        self.console.print(Text(f"  {text}", style=style))

    def _result(
        self, outcome: Outcome, headline: str, detail: str | None = None
    ) -> None:
        """The end of a flow: a blank line, `✓ headline`, then a muted detail."""
        self._gap()
        self.console.print(
            Text.assemble(
                ("  " + _OUTCOME_GLYPHS[outcome] + " ", outcome),
                (headline, "bold"),
            )
        )
        if detail:
            self.console.print(Text(f"    {detail}", style="muted"))

    def _status_result(
        self, title: str, status: ConnectorStatusEvent, *, signed_in: bool = False
    ) -> None:
        if status.status == "connected":
            how = "Signed in. " if signed_in else ""
            self._result(
                "ok",
                f"{title} connected.",
                f"{how}{status.tools} tools are available now.",
            )
        elif status.status == "needs_auth":
            self._result("warn", f"{title} needs sign-in.", "Sign in from /connectors.")
        else:
            self._result("err", f"{title}: {status.status}", status.error)


def connector_row(info: ConnectorInfo, width: int = 0) -> Row:
    """`● GitHub   12 tools · reads auto, writes ask`, coloured by status."""
    glyph, glyph_style = _GLYPHS.get(info.status, ("○", "muted"))
    title = sanitize_display_text(info.title).ljust(width)
    usable = [t for t in info.tools if t.effective != "off"]
    if info.status == "failed":
        detail = f"failed: {info.error or 'not connected'}"
        if info.retry_in_s:
            detail += f" (retrying in {info.retry_in_s}s)"
        detail_style = "err"
    elif not info.enabled:
        detail, detail_style = "disabled", "muted"
    elif info.status == "needs_auth":
        detail, detail_style = "needs sign-in", "warn"
    else:
        reads = "reads auto" if info.trust_read_only_hints else "reads ask"
        detail, detail_style = f"{len(usable)} tools · {reads}, writes ask", "muted"
    return [
        (f"class:{glyph_style}", f"{glyph} "),
        ("", title),
        ("", "  "),
        (f"class:{detail_style}", sanitize_display_text(detail)),
    ]


def list_label(info: ConnectorInfo) -> str:
    return row_text(connector_row(info))


def catalog_rows(catalog: Sequence[CatalogItem], width: int = 100) -> list[Row]:
    """The catalogue in three aligned columns, then `Custom URL…`."""
    title_w = max([len(CUSTOM_LABEL), *(len(i.title) for i in catalog)])
    room = max(12, width - 2 - title_w - 2 - 3 - _TAG_WIDTH)
    purposes = [_fit(sanitize_display_text(i.purpose), room) for i in catalog]
    purpose_w = max((len(p) for p in purposes), default=0)
    rows: list[Row] = []
    for item, purpose in zip(catalog, purposes, strict=True):
        tag, tag_style = (
            ("✓ added", "ok") if item.added else _AUTH_TAGS.get(item.auth, ("", ""))
        )
        rows.append(
            [
                (
                    "class:muted" if item.added else "",
                    sanitize_display_text(item.title).ljust(title_w),
                ),
                ("", "  "),
                ("class:muted", purpose.ljust(purpose_w)),
                ("", "   "),
                (f"class:{tag_style}", tag),
            ]
        )
    rows.append(
        [
            ("", CUSTOM_LABEL.ljust(title_w)),
            ("", "  "),
            ("class:muted", "any MCP server address"),
        ]
    )
    return rows


def catalog_label(item: CatalogItem) -> str:
    return row_text(catalog_rows([item])[0])


def tool_row(tool: ConnectorToolInfo, width: int = 0) -> Row:
    kind = "read" if tool.read_only else "write"
    style = {"auto": "ok", "ask": "accent", "off": "muted"}.get(tool.effective, "")
    return [
        ("", sanitize_display_text(tool.name).ljust(width)),
        ("", "  "),
        ("class:muted", f"{kind} · "),
        (f"class:{style}" if style else "", tool.effective),
    ]


def tool_label(tool: ConnectorToolInfo) -> str:
    return row_text(tool_row(tool))


def probe_summary(title: str, probe: ConnectorProbeEvent) -> str:
    """`<Title> has N tools: R read (run automatically), W write (ask first).`"""
    total = probe.read + probe.write
    return (
        f"{title} has {total} tools: {probe.read} read (run automatically), "
        f"{probe.write} write (ask first)."
    )


def _fit(text: str, room: int) -> str:
    return text if len(text) <= room else text[: room - 1].rstrip() + "…"


def _cannot_open_browser() -> bool:
    # Over SSH a browser would open on the far machine, if anywhere.
    return bool(os.environ.get("SSH_CONNECTION"))


async def _cancel(task: asyncio.Task[None] | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
