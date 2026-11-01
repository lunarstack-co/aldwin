"""`/connectors`: adding a local command, its approval, and how local rows read."""

from __future__ import annotations

from typing import Any

from aldwin.cli.connectors_view import (
    IMPORT_LABEL,
    IMPORT_NONE_FOUND,
    LOCAL_LABEL,
    LOCAL_NOTE,
    connector_row,
)
from aldwin.cli.picker import row_text
from aldwin.client import ProtocolError
from aldwin.protocol import (
    ApprovalEvent,
    ConnectorInfo,
    ConnectorProbeEvent,
    ConnectorStatusEvent,
)

from .test_connectors_view import FakeConnectorsClient, _run

COMMAND = "npx -y @modelcontextprotocol/server-everything"


class FakeLocalClient(FakeConnectorsClient):
    """Answers a local add like the host: a dry run, then one approval."""

    def __init__(self) -> None:
        super().__init__()
        self.answers: list[str] = []

    async def connectors_add(self, **kw: Any) -> Any:
        if kw.get("command") is None:
            return await super().connectors_add(**kw)
        self.calls.append(("add", kw))
        if kw.get("dry_run"):
            return ConnectorProbeEvent(re="r", name="everything", title="Everything")
        event = ApprovalEvent(
            re="r",
            approval_id="a1",
            tool="connectors.add",
            summary=f"Run a local connector: {kw['name']}",
            detail=f"Command: {COMMAND}\nFolder: /ws\nEnvironment: HOME, PATH",
            risky=["runs a program on this computer", "package version not pinned"],
        )
        answer, _reason = await kw["on_approval"](event)
        self.answers.append(answer)
        if answer != "yes":
            raise ProtocolError(
                "Everything was not approved, so nothing was saved.", code="connector"
            )
        return ConnectorStatusEvent(
            re="r", name=kw["name"], status="connected", tools=3
        )


async def test_local_flow_enter_means_no() -> None:
    client = FakeLocalClient()
    # Add a connector -> Local command -> the command -> keep the name -> Enter.
    out = await _run(client, "1", "2", COMMAND, "", "")
    assert LOCAL_LABEL in out
    assert LOCAL_NOTE in out
    assert client.answers == ["no"]
    assert "runs a program on this computer" in out
    assert "Command: " in out and "Folder: " in out and "Environment: " in out
    assert "Everything was not approved, so nothing was saved." in out
    dry, real = (call[1] for call in client.calls)
    assert dry == {"command": COMMAND, "dry_run": True}
    assert (real["command"], real["name"]) == (COMMAND, "everything")


async def test_local_flow_yes_connects() -> None:
    client = FakeLocalClient()
    out = await _run(client, "1", "2", COMMAND, "mine", "y")
    assert client.answers == ["yes"]
    assert client.calls[1][1]["name"] == "mine"
    assert "Mine connected." in out


async def test_failed_and_note_rows() -> None:
    failed = ConnectorInfo(
        name="everything",
        title="Everything",
        transport="stdio",
        status="failed",
        error="command not found: npx",
    )
    text = row_text(connector_row(failed))
    assert text == "✕ Everything  failed: command not found: npx"

    imported = ConnectorInfo(
        name="github",
        title="Github",
        transport="stdio",
        enabled=False,
        note="review and enable",
    )
    row = connector_row(imported)
    assert row_text(row).endswith("disabled · review and enable")
    assert row[-1] == ("class:muted", " · review and enable")


async def test_enable_passes_the_approval_handler() -> None:
    client = FakeConnectorsClient(
        [ConnectorInfo(name="github", title="Github", transport="stdio", enabled=False)]
    )
    seen: dict[str, Any] = {}
    original = client.connectors_enable

    async def enable(name: str, *, on_approval: Any = None) -> Any:
        seen["handler"] = on_approval
        return await original(name, on_approval=on_approval)

    client.connectors_enable = enable  # type: ignore[method-assign]
    await _run(client, "1", "3")  # Github -> Enable
    assert seen["handler"] is not None


async def test_arrow_keys_move_the_highlight_and_choose() -> None:
    import io

    from prompt_toolkit.input import create_pipe_input

    from aldwin.cli.connectors_view import _View
    from aldwin.cli.render import ChatRenderer
    from aldwin.cli.theme import create_console

    buf = io.StringIO()
    console = create_console(width=100, file=buf, force_terminal=True)
    assert console.is_interactive
    event = ApprovalEvent(
        re="r",
        approval_id="a1",
        tool="connectors.add",
        summary="Run a local connector: everything",
        detail=f"Command: {COMMAND}",
        risky=["runs a program on this computer"],
    )

    async def no_prompt(_label: str) -> str:
        raise AssertionError("the key reader answers, not a typed line")

    with create_pipe_input() as pipe:
        view = _View(
            FakeLocalClient(), ChatRenderer(console), no_prompt, no_prompt, pipe
        )
        pipe.send_text("\x1b[B")
        pipe.send_text("\r")
        answer = await view.approve(event)

    assert answer == ("yes", None)
    out = buf.getvalue()
    # Redrawn after the Down key: the ❯ moved from No to Yes.
    assert out.index("❯ No") < out.rindex("❯ Yes")
    assert "Starting the program…" in out


def _scan(*found: Any, warnings: list[str] | None = None) -> Any:
    from aldwin.protocol import ConnectorsImportEvent

    return ConnectorsImportEvent(re="r", found=list(found), warnings=warnings or [])


async def test_import_menu_lists_found_asks_and_imports() -> None:
    from aldwin.protocol import ConnectorsImportEvent, ImportRowInfo, ImportSourceInfo

    client = FakeConnectorsClient()
    cursor = ImportSourceInfo(app="Cursor", path="/home/me/.cursor/mcp.json", servers=2)
    broken = ImportSourceInfo(
        app="Zed",
        path="/home/me/.config/zed/settings.json",
        error="it isn't UTF-8 text",
    )
    client.import_answers = [
        _scan(cursor, broken, warnings=["Goose: can't resolve {workspace}/x"]),
        ConnectorsImportEvent(
            re="r",
            found=[cursor],
            rows=[
                ImportRowInfo(
                    name="github",
                    kind="local",
                    status="imported",
                    detail="imported",
                    app="Cursor",
                ),
                ImportRowInfo(
                    name="docs",
                    kind="remote",
                    status="skipped",
                    detail="skipped: same as docs",
                    app="Cursor",
                ),
            ],
            imported=1,
            values=1,
        ),
    ]
    # No connectors, so row 1 is Add and row 2 is Import; then y.
    out = await _run(client, "2", "y")
    assert IMPORT_LABEL in out
    assert "warning: Goose: can't resolve {workspace}/x" in out
    assert "Cursor (user)  2 servers  /home/me/.cursor/mcp.json" in out
    assert "Zed (user)  can't read: it isn't UTF-8 text" in out
    assert "github  local  imported  (Cursor)" in out
    assert "Imported 1 connector. 1 value moved to Aldwin's credential store." in out
    assert "Local commands are off until you enable and approve them." in out
    scan, real = (call[1] for call in client.calls if call[0] == "import")
    assert scan["dry_run"] is True
    assert real["paths"] == ["/home/me/.cursor/mcp.json"]


async def test_import_menu_enter_imports_nothing() -> None:
    from aldwin.protocol import ImportSourceInfo

    client = FakeConnectorsClient()
    client.import_answers = [
        _scan(
            ImportSourceInfo(app="Cursor", path="/home/me/.cursor/mcp.json", servers=2)
        )
    ]
    out = await _run(client, "2", "")
    assert "Nothing imported." in out
    assert [c for c in client.calls if c[0] == "import"][1:] == []


async def test_import_menu_with_nothing_found() -> None:
    client = FakeConnectorsClient()
    client.import_answers = [_scan()]
    out = await _run(client, "2")
    assert IMPORT_NONE_FOUND in out
