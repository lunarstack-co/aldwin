"""`/connectors`: list, add and manage connectors."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncGenerator, Awaitable, Callable, Collection, Sequence
from typing import Literal, Protocol

from prompt_toolkit.input import Input
from rich.text import Text

from aldwin.cli.connector_auth import answer_connector_auth
from aldwin.cli.picker import Row, row_text
from aldwin.cli.render import ChatRenderer
from aldwin.client import ProtocolError, sanitize_display_text
from aldwin.protocol import (
    ApprovalAnswer,
    ApprovalEvent,
    CatalogItem,
    ConnectorAuth,
    ConnectorAuthEvent,
    ConnectorInfo,
    ConnectorProbeEvent,
    ConnectorsEvent,
    ConnectorsImportEvent,
    ConnectorStatusEvent,
    ConnectorToolInfo,
    DoneEvent,
    ErrorEvent,
    Event,
    NoticeEvent,
)

PromptLine = Callable[[str], Awaitable[str]]
ApprovalHandler = Callable[
    [ApprovalEvent], Awaitable[tuple[ApprovalAnswer, str | None]]
]
ToolChoice = Literal["auto", "ask", "off", "default"]

ADD_LABEL = "+ Add a connector…"
IMPORT_LABEL = "Import from other apps…"
IMPORT_NONE_FOUND = "No MCP config files found in the usual places."
CUSTOM_LABEL = "Custom URL…"
LOCAL_LABEL = "Local command… (advanced)"
LOCAL_NOTE = (
    "This runs a program on this computer with access to your workspace folder."
)
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
CATEGORY_HEADINGS = {
    "work": "Work and projects",
    "developer": "Developer",
    "payments": "Payments",
    "design": "Design and media",
    "automation": "Automation",
    "data": "Data and analytics",
    "search": "Search and knowledge",
}
Outcome = Literal["ok", "warn", "err"]
_OUTCOME_GLYPHS: dict[Outcome, str] = {"ok": "✓", "warn": "!", "err": "✗"}


class ConnectorsClient(Protocol):
    """The client methods this view uses (satisfied by `AldwinClient`)."""

    async def connectors_list(self) -> ConnectorsEvent: ...

    async def connectors_import(
        self,
        paths: Sequence[str] = (),
        *,
        dry_run: bool = False,
        rename_conflicts: bool = False,
    ) -> ConnectorsImportEvent: ...

    async def connectors_add(
        self,
        *,
        url: str | None = None,
        name: str | None = None,
        title: str | None = None,
        auth: ConnectorAuth = "none",
        token: str | None = None,
        token_env: str | None = None,
        dry_run: bool = False,
        command: str | None = None,
        on_approval: ApprovalHandler | None = None,
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

    async def connectors_enable(
        self, name: str, *, on_approval: ApprovalHandler | None = None
    ) -> ConnectorStatusEvent: ...

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
        rows.append([("class:accent", IMPORT_LABEL)])
        choice = await self._choose(rows, title="Connectors")
        if choice is None:
            return
        if choice == len(listing.items):
            await self.add(listing.catalog)
        elif choice == len(listing.items) + 1:
            await self.import_from_apps()
        else:
            await self.manage(listing.items[choice])

    # --- import -------------------------------------------------------------

    async def import_from_apps(self) -> None:
        """Show what other apps have set up, ask once, and import it."""
        scan = await self.client.connectors_import(dry_run=True)
        self._warnings(scan.warnings)
        if not scan.found:
            self._result("warn", IMPORT_NONE_FOUND)
            return
        self._gap()
        self._line("Found:", "bold")
        for item in scan.found:
            state = (
                f"can't read: {item.error}"
                if item.error
                else f"{item.servers} server{'' if item.servers == 1 else 's'}"
            )
            self._line(
                sanitize_display_text(
                    f"{item.app} ({item.scope})  {state}  {item.path}"
                ),
                "muted" if item.error or not item.servers else "",
            )
        usable = [f.path for f in scan.found if not f.error and f.servers]
        if not usable:
            self._result("warn", import_summary(0, 0))
            return
        reply = (await self.prompt_line("  Import these? [y/N] ")).strip().lower()
        if reply not in ("y", "yes"):
            self._line("Nothing imported.", "muted")
            return
        result = await self.client.connectors_import(usable)
        self._warnings(result.warnings)
        self._gap()
        for row in result.rows:
            style = {"imported": "ok", "skipped": "muted", "failed": "err"}[row.status]
            self._line(
                sanitize_display_text(
                    f"{row.name}  {row.kind}  {row.detail}  ({row.app})"
                ),
                style,
            )
        self._result(
            "ok" if result.imported else "warn",
            import_summary(result.imported, result.values),
            "Local commands are off until you enable and approve them."
            if result.imported
            else None,
        )

    def _warnings(self, warnings: Sequence[str]) -> None:
        for warning in warnings:
            self._line(f"warning: {sanitize_display_text(warning)}", "warn")

    # --- add ----------------------------------------------------------------

    async def add(self, catalog: Sequence[CatalogItem] = ()) -> None:
        rows, choices = catalog_menu(catalog, self.console.width)
        headings = {i for i, choice in enumerate(choices) if choice is None}
        index = await self._choose(rows, title="Add a connector", disabled=headings)
        if index is None:
            return
        choice = choices[index]
        if isinstance(choice, CatalogItem):
            await self.add_from_catalog(choice)
        elif choice == "custom":
            await self.add_custom()
        elif choice == "local":
            await self.add_local()

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

    async def add_local(self) -> None:
        """Add a server started from a command; the host asks before it runs."""
        self._line(LOCAL_NOTE, "muted")
        line = (await self.prompt_line("  Command: ")).strip()
        if not line:
            return
        try:
            probe = await self.client.connectors_add(command=line, dry_run=True)
        except ProtocolError as exc:
            self._result("err", "Couldn't use that command", str(exc))
            return
        assert isinstance(probe, ConnectorProbeEvent)
        typed = (await self.prompt_line(f"  Name [{probe.name}]: ")).strip()
        name = typed or probe.name
        title = probe.title if name == probe.name else name[:1].upper() + name[1:]
        try:
            status = await self.client.connectors_add(
                command=line, name=name, on_approval=self.approve
            )
        except ProtocolError as exc:
            self._result("err", f"{title} didn't connect", str(exc))
            return
        assert isinstance(status, ConnectorStatusEvent)
        self._status_result(title, status)

    async def approve(self, event: ApprovalEvent) -> tuple[ApprovalAnswer, str | None]:
        """Show the host's question; Enter, Esc and n all mean no."""
        if self.input_dev is not None:
            answer = await self._read_choice(event, self.input_dev)
        else:
            self.renderer.render_approval_panel(event)
            reply = await self.prompt_line("  Run this program? [y/N] ")
            answer = "yes" if reply.strip().lower() in ("y", "yes") else "no"
        if answer == "yes":
            # Starting can take a while (npx may download first); say so.
            self._line("Starting the program…", "muted")
            return "yes", None
        return "no", None

    async def _read_choice(self, event: ApprovalEvent, input_dev: Input) -> str:
        """Read the answer, moving the ❯ with the arrow keys as it changes."""
        from aldwin.cli.chat_screen import read_approval_key

        if self.renderer.plain or not self.console.is_interactive:
            self.renderer.render_approval_panel(event)
            return await read_approval_key(input_dev, grantable=False)
        from rich.live import Live

        self.renderer.notify_approval(event)
        panel = self.renderer.build_approval_panel
        with Live(
            panel(event, selected=0), console=self.console, auto_refresh=False
        ) as live:
            return await read_approval_key(
                input_dev,
                grantable=False,
                on_select=lambda row: live.update(
                    panel(event, selected=row), refresh=True
                ),
            )

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
        await answer_connector_auth(
            event,
            client=self.client,
            line=self._line,
            gap=self._gap,
            prompt_line=self.prompt_line,
            prompt_secret=self.prompt_secret,
        )

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
            try:
                status = await self.client.connectors_enable(
                    info.name, on_approval=self.approve
                )
            except ProtocolError as exc:
                self._result("err", f"{title} wasn't enabled", str(exc))
                return
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

    async def _choose(
        self,
        rows: Sequence[Row],
        *,
        title: str,
        disabled: Collection[int] = (),
    ) -> int | None:
        """Arrow-key picker on a terminal, otherwise a numbered list.

        Rows in `disabled` are headings: shown, never chosen, never numbered.
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
                disabled=disabled,
            )
            if choice is not None:
                picked = row_text(rows[choice]).split("  ")[0].strip()
                self._line(f"{title} › {picked}", "muted")
            return choice
        self._gap()
        self._line(title, "bold")
        numbered: list[int] = []
        for idx, row in enumerate(rows):
            if idx in disabled:
                self.console.print(Text(f"  {row_text(row)}".rstrip(), style="bold"))
                continue
            numbered.append(idx)
            self.console.print(Text(f"  {len(numbered)}. {row_text(row)}".rstrip()))
        raw = (await self.prompt_line("Choose a number (Enter to cancel): ")).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(numbered):
            return numbered[int(raw) - 1]
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
    note: list[tuple[str, str]] = (
        [("class:muted", f" · {sanitize_display_text(info.note)}")] if info.note else []
    )
    return [
        (f"class:{glyph_style}", f"{glyph} "),
        ("", title),
        ("", "  "),
        (f"class:{detail_style}", sanitize_display_text(detail)),
        *note,
    ]


def import_summary(imported: int, values: int) -> str:
    connectors = "connector" if imported == 1 else "connectors"
    moved = "value" if values == 1 else "values"
    return (
        f"Imported {imported} {connectors}. "
        f"{values} {moved} moved to Aldwin's credential store."
    )


def list_label(info: ConnectorInfo) -> str:
    return row_text(connector_row(info))


def catalog_rows(catalog: Sequence[CatalogItem], width: int = 100) -> list[Row]:
    """The catalogue in three aligned columns, then `Custom URL…` and the local
    command entry.
    """
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
    rows.append([("", LOCAL_LABEL)])
    return rows


CatalogChoice = CatalogItem | Literal["custom", "local"] | None


def catalog_menu(
    catalog: Sequence[CatalogItem], width: int = 100
) -> tuple[list[Row], list[CatalogChoice]]:
    """The catalogue under its category headings, then `Custom URL…` and the local
    entry. `None` in the choices marks a heading row. A catalogue without
    categories, from an older host, stays one flat list.
    """
    rows = catalog_rows(catalog, width)
    app_rows, extra_rows = rows[: len(catalog)], rows[len(catalog) :]
    grouped = any(item.category for item in catalog)
    menu_rows: list[Row] = []
    choices: list[CatalogChoice] = []
    current: str | None = None
    for item, row in zip(catalog, app_rows, strict=True):
        if grouped and item.category != current:
            current = item.category
            heading = CATEGORY_HEADINGS.get(current or "", "Other")
            menu_rows.append([("class:heading", heading)])
            choices.append(None)
        menu_rows.append(row)
        choices.append(item)
    menu_rows += extra_rows
    choices += ["custom", "local"]
    return menu_rows, choices


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
