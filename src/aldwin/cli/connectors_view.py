"""`/connectors` (and `/mcp`): list, add and manage connectors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Literal, Protocol

from prompt_toolkit.input import Input
from rich.text import Text

from aldwin.cli.render import ChatRenderer
from aldwin.client import ProtocolError, sanitize_display_text
from aldwin.protocol import (
    ConnectorAuth,
    ConnectorInfo,
    ConnectorProbeEvent,
    ConnectorsEvent,
    ConnectorStatusEvent,
    ConnectorToolInfo,
)

PromptLine = Callable[[str], Awaitable[str]]
ToolChoice = Literal["auto", "ask", "off", "default"]

ADD_LABEL = "+ Add a connector…"
_GLYPHS = {"connected": "●", "failed": "✕", "disabled": "○", "connecting": "…"}


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
            self._say(listing.error, "warn")
        labels = [list_label(info) for info in listing.items] + [ADD_LABEL]
        self._say("Connectors:", "muted")
        choice = await self._choose(labels)
        if choice is None:
            return
        if choice == len(listing.items):
            await self.add()
        else:
            await self.manage(listing.items[choice])

    # --- add ----------------------------------------------------------------

    async def add(self) -> None:
        url = (await self.prompt_line("Server URL: ")).strip()
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
                    self._say(str(exc), "err")
                    return
                self._say(str(exc), "warn")
                token = (await self.prompt_secret("Token (hidden): ")).strip()
                if not token:
                    return
                auth = "bearer"
        assert isinstance(probe, ConnectorProbeEvent)

        typed = (await self.prompt_line(f"Name [{probe.name}]: ")).strip()
        name = typed or probe.name
        title = probe.title if name == probe.name else name[:1].upper() + name[1:]
        self._say(probe_summary(title, probe), "fg")
        if await self._choose([f"Add {title}", "Cancel"]) != 0:
            self._say("Nothing added.", "muted")
            return

        try:
            status = await self.client.connectors_add(
                url=url, name=name, auth=auth, token=token
            )
        except ProtocolError as exc:
            self._say(str(exc), "err")
            return
        assert isinstance(status, ConnectorStatusEvent)
        self._say(f"{title} connected. {status.tools} tools are available now.", "ok")

    # --- manage -------------------------------------------------------------

    async def manage(self, info: ConnectorInfo) -> None:
        trust = "on" if info.trust_read_only_hints else "off"
        actions = [
            "Tools",
            "Reconnect",
            "Enable" if not info.enabled else "Disable",
            "Remove",
            f"Trust read-only hints: {trust}",
        ]
        self._say(list_label(info), "muted")
        choice = await self._choose(actions)
        if choice == 0:
            await self.tools(info)
        elif choice == 1:
            await self.client.connectors_reconnect(info.name)
            self._say(f"Reconnecting {info.title}…", "muted")
        elif choice == 2 and info.enabled:
            await self.client.connectors_disable(info.name)
            self._say(f"{info.title} disabled.", "muted")
        elif choice == 2:
            status = await self.client.connectors_enable(info.name)
            self._say(_status_line(info.title, status), "muted")
        elif choice == 3:
            self._say(f"Remove {info.title} and its saved token?", "warn")
            if await self._choose(["No", "Yes"]) == 1:
                await self.client.connectors_remove(info.name)
                self._say(f"{info.title} removed.", "muted")
        elif choice == 4:
            await self.client.connectors_trust_hints(
                info.name, not info.trust_read_only_hints
            )
            now = "off" if info.trust_read_only_hints else "on"
            self._say(f"Trust read-only hints: {now}.", "muted")

    async def tools(self, info: ConnectorInfo) -> None:
        if not info.tools:
            self._say(f"{info.title} has no tools listed yet.", "muted")
            return
        choice = await self._choose([tool_label(t) for t in info.tools])
        if choice is None:
            return
        tool = info.tools[choice]
        settings: list[ToolChoice] = ["auto", "ask", "off", "default"]
        picked = await self._choose(
            ["auto (run without asking)", "ask", "off (hide from Aldwin)", "default"]
        )
        if picked is None:
            return
        await self.client.connectors_tool_policy(info.name, tool.name, settings[picked])
        self._say(f"{tool.name}: {settings[picked]}.", "muted")

    # --- helpers ------------------------------------------------------------

    async def _choose(self, labels: Sequence[str]) -> int | None:
        """Arrow-key picker on a terminal, otherwise a numbered list."""
        from aldwin.cli.picker import pick_index

        interactive = (
            isinstance(self.input_dev, Input)
            and self.console.is_interactive
            and not self.renderer.plain
        )
        if interactive:
            return await pick_index(
                labels, input_dev=self.input_dev, plain=self.renderer.plain
            )
        for idx, label in enumerate(labels, start=1):
            self.console.print(Text(f"  {idx}. {label}"))
        raw = (await self.prompt_line("Choose a number (Enter to cancel): ")).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(labels):
            return int(raw) - 1
        return None

    def _say(self, text: str, style: str) -> None:
        self.console.print(Text(text, style=style))


def list_label(info: ConnectorInfo) -> str:
    """`● GitHub  12 tools · reads auto, writes ask`."""
    glyph = _GLYPHS.get(info.status, "○")
    usable = [t for t in info.tools if t.effective != "off"]
    title = sanitize_display_text(info.title)
    if info.status == "failed":
        detail = f"failed: {info.error or 'not connected'}"
        if info.retry_in_s:
            detail += f" (retrying in {info.retry_in_s}s)"
        return f"{glyph} {title}  {detail}"
    if not info.enabled:
        return f"{glyph} {title}  disabled"
    reads = "reads auto" if info.trust_read_only_hints else "reads ask"
    return f"{glyph} {title}  {len(usable)} tools · {reads}, writes ask"


def tool_label(tool: ConnectorToolInfo) -> str:
    kind = "read" if tool.read_only else "write"
    return f"{sanitize_display_text(tool.name)}  {kind} · {tool.effective}"


def probe_summary(title: str, probe: ConnectorProbeEvent) -> str:
    """`<Title> has N tools: R read (run automatically), W write (ask first).`"""
    total = probe.read + probe.write
    return (
        f"{title} has {total} tools: {probe.read} read (run automatically), "
        f"{probe.write} write (ask first)."
    )


def _status_line(title: str, status: ConnectorStatusEvent) -> str:
    if status.status == "connected":
        return f"{title} connected. {status.tools} tools are available now."
    return f"{title}: {status.status}" + (f" ({status.error})" if status.error else "")
