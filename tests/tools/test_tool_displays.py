"""Per-tool display payload tests across all built-in and connector tools."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from fixtures.mcp_server import FixtureControls
from openpyxl import Workbook

from aldwin.core.connectors.config import ConnectorConfig
from aldwin.core.connectors.connection import McpConnection
from aldwin.core.connectors.mcp_tool import McpTool, RemoteToolSpec
from aldwin.core.connectors.policy import connector_policy
from aldwin.core.policy import (
    ApprovalRequest,
    PolicySession,
    register_runtime_policy,
    unregister_runtime_policy,
    wrap_tools_with_policy,
)
from aldwin.core.tools.calculate import CalculateTool
from aldwin.core.tools.documents import ReadDocumentTool
from aldwin.core.tools.edits import EditFileTool, WriteFileTool
from aldwin.core.tools.files import GlobTool, ListDirTool, ReadFileTool
from aldwin.core.tools.memory_tools import build_memory_tools
from aldwin.core.tools.persona_tool import SetPersonaTool
from aldwin.core.tools.run_command import RunCommandTool
from aldwin.core.tools.skill_tools import CreateSkillTool
from aldwin.core.tools.todo import TodoTool
from aldwin.core.tools.web_fetch import WebFetchTool
from aldwin.core.tools.web_search import TavilyProvider, WebSearchTool
from aldwin.core.workspace import Workspace

DOCS_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "docs"


async def _allow_ask(_summary: str, _detail: str = "") -> bool:
    return True


def test_run_command_approval_display(workspace: Workspace) -> None:
    """`run_command.prepare_approval` builds a `terminal` display with empty streams."""
    tool = RunCommandTool(_allow_ask, cwd=workspace.root, wrapped_by_policy=True)
    preview = tool.prepare_approval("ls -la")
    assert preview.display == {
        "kind": "terminal",
        "command": "ls -la",
        "cwd": str(workspace.root),
        "stdout": "",
        "stderr": "",
        "exit_code": None,
        "duration_ms": 0,
        "truncated": False,
    }


async def test_run_command_result_display_keeps_output_in_order_with_exit_code(
    workspace: Workspace,
) -> None:
    """stdout and stderr arrive as one stream in the order the command wrote them."""
    tool = RunCommandTool(_allow_ask, cwd=workspace.root, wrapped_by_policy=True)
    out = await tool.forward("printf 'a\\n'; printf 'e\\n' >&2; printf 'b'; exit 3")
    assert out.ok is False
    assert out.display is not None
    assert out.display["kind"] == "terminal"
    assert out.display["stdout"] == "a\ne\nb"
    assert out.display["stderr"] == ""
    assert out == "exit 3\na\ne\nb"
    assert out.display["exit_code"] == 3
    assert out.display["duration_ms"] >= 0


async def test_edit_file_one_line_change_carries_diff_on_approval_and_result(
    workspace: Workspace,
    aldwin_home: Path,
) -> None:
    """`edit_file` attaches matching `diff` displays to both preview and result."""
    session = PolicySession(workspace=workspace, home=aldwin_home)
    notes = workspace.root / "notes.md"
    notes.write_text("x\ny\n", encoding="utf-8")
    session.mark_read(notes)

    tool = EditFileTool(workspace, session)
    preview = tool.prepare_approval("notes.md", "y", "z")
    assert preview.display is not None
    assert preview.display["kind"] == "diff"
    assert preview.display["path"] == "notes.md"
    assert preview.display["additions"] == 1
    assert preview.display["deletions"] == 1
    assert preview.display["lines"] == [
        {"kind": "context", "text": "x"},
        {"kind": "removed", "text": "y"},
        {"kind": "added", "text": "z"},
    ]

    result = await tool.forward("notes.md", "y", "z")
    assert result.display == preview.display


async def test_write_file_new_file_carries_three_additions(
    workspace: Workspace,
    aldwin_home: Path,
) -> None:
    """`write_file` on a new 3-line file reports 3 additions and 0 deletions."""
    session = PolicySession(workspace=workspace, home=aldwin_home)
    tool = WriteFileTool(workspace, session)

    preview = tool.prepare_approval("fresh.txt", "one\ntwo\nthree\n")
    assert preview.display is not None
    assert preview.display["additions"] == 3
    assert preview.display["deletions"] == 0

    result = await tool.forward("fresh.txt", "one\ntwo\nthree\n")
    assert result.display == preview.display


async def test_memory_tools_build_memory_and_diff_displays(aldwin_home: Path) -> None:
    """`remember` and `forget` emit `memory` results and `diff` approvals."""
    remember_tool, _recall_tool, forget_tool = build_memory_tools(home=aldwin_home)

    saved = await remember_tool.forward("Prefers concise answers")
    assert saved.display == {
        "kind": "memory",
        "changes": [{"text": "Prefers concise answers", "change": "added"}],
    }

    note_dir = aldwin_home / "memory" / "notes"
    note_file = next(note_dir.glob("*.md"))
    content = note_file.read_text(encoding="utf-8")
    note_id = content.split("id=")[1].split()[0].rstrip("-->")

    rep_preview = remember_tool.prepare_approval(
        "Prefers bullet lists", replace=note_id
    )
    assert rep_preview.display is not None
    assert rep_preview.display["kind"] == "diff"
    assert rep_preview.display["additions"] == 1
    assert rep_preview.display["deletions"] == 1

    updated = await remember_tool.forward("Prefers bullet lists", replace=note_id)
    assert updated.display == {
        "kind": "memory",
        "changes": [{"text": "Prefers bullet lists", "change": "replaced"}],
    }

    del_preview = forget_tool.prepare_approval(note_id)
    assert del_preview.display is not None
    assert del_preview.display["kind"] == "diff"
    assert del_preview.display["deletions"] == 1

    removed = await forget_tool.forward(note_id)
    assert removed.display == {
        "kind": "memory",
        "changes": [{"text": "Prefers bullet lists", "change": "removed"}],
    }


async def test_persona_and_create_skill_have_diff_approvals_and_no_result_display(
    aldwin_home: Path,
) -> None:
    """`set_persona` and `create_skill` attach `diff` previews and no result display."""
    persona_tool = SetPersonaTool(_allow_ask, home=aldwin_home, wrapped_by_policy=True)
    p_preview = persona_tool.prepare_approval("Speak like a calm mentor.")
    assert p_preview.display is not None
    assert p_preview.display["kind"] == "diff"
    assert p_preview.display["path"] == "persona.md"

    p_out = await persona_tool.forward("Speak like a calm mentor.")
    assert getattr(p_out, "display", None) is None

    skill_tool = CreateSkillTool(home=aldwin_home)
    s_preview = skill_tool.prepare_approval(
        name="tidy-notes",
        description="Tidy markdown notes.",
        instructions="Keep headings consistent.",
    )
    assert s_preview.display is not None
    assert s_preview.display["kind"] == "diff"
    assert s_preview.display["path"] == "skills/tidy-notes/SKILL.md"

    s_out = await skill_tool.forward(
        name="tidy-notes",
        description="Tidy markdown notes.",
        instructions="Keep headings consistent.",
    )
    assert s_out.display is None


async def test_list_dir_and_glob_cap_at_300_and_omit_blocked_files(
    workspace: Workspace,
    aldwin_home: Path,
) -> None:
    """`list_dir` and `glob` return capped `files` displays without `.env`."""
    session = PolicySession(workspace=workspace, home=aldwin_home)
    (workspace.root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    for idx in range(500):
        (workspace.root / f"f_{idx:03d}.txt").write_text("x\n", encoding="utf-8")

    list_tool = ListDirTool(workspace, session)
    listed = await list_tool.forward(".")
    assert listed.display is not None
    assert listed.display["kind"] == "files"
    assert len(listed.display["entries"]) == 300
    assert listed.display["truncated"] is True
    assert all(e["path"] != ".env" for e in listed.display["entries"])

    glob_tool = GlobTool(workspace, session)
    globbed = await glob_tool.forward("*")
    assert globbed.display is not None
    assert globbed.display["kind"] == "files"
    assert len(globbed.display["entries"]) == 300
    assert globbed.display["truncated"] is True
    assert all(e["kind"] == "file" for e in globbed.display["entries"])
    assert all(e["path"] != ".env" for e in globbed.display["entries"])


async def test_web_search_caps_results_at_10_with_mock_transport(
    aldwin_home: Path,
) -> None:
    """`web_search` returns a `search` display with up to 10 hits."""

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": f"Result {i}",
                        "url": f"https://example.com/{i}",
                        "content": f"Snippet {i}",
                    }
                    for i in range(12)
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        provider = TavilyProvider(api_key="tvly-test", client=http)
        tool = WebSearchTool(home=aldwin_home, provider=provider)
        out = await tool.forward("python typing", limit=10)

    assert out.display is not None
    assert out.display["kind"] == "search"
    assert out.display["query"] == "python typing"
    assert len(out.display["results"]) == 10
    assert out.display["results"][0] == {
        "title": "Result 0",
        "url": "https://example.com/0",
        "snippet": "Snippet 0",
    }


async def test_web_fetch_extracts_page_display() -> None:
    """`web_fetch` populates `{url, title, site, description}` from HTML metadata."""
    html_body = (
        "<!doctype html><html><head>"
        "<title>  Example Guide  </title>"
        '<meta name="description" content="A crisp summary of the guide.">'
        "</head><body><main><p>Body text here.</p></main></body></html>"
    )

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=html_body.encode("utf-8"),
        )

    async def public_resolver(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = WebFetchTool(
            client=client,
            resolver=public_resolver,
            wrapped_by_policy=True,
        )
        out = await tool.forward("https://example.com/guide")

    assert out.display == {
        "kind": "page",
        "url": "https://example.com/guide",
        "title": "Example Guide",
        "site": "example.com",
        "description": "A crisp summary of the guide.",
    }


async def test_read_document_table_displays_for_csv_xlsx_and_none_for_pdf(
    workspace: Workspace,
    aldwin_home: Path,
) -> None:
    """`read_document` emits capped `table` for CSV/XLSX and `None` for PDF."""
    session = PolicySession(workspace=workspace, home=aldwin_home)
    tool = ReadDocumentTool(workspace, session)

    csv_lines = [",".join(f"h{c}" for c in range(25))]
    for r in range(250):
        csv_lines.append(",".join(f"r{r}c{c}" for c in range(25)))
    (workspace.root / "big.csv").write_text("\n".join(csv_lines), encoding="utf-8")

    csv_out = await tool.forward("big.csv")
    assert csv_out.display is not None
    assert csv_out.display["kind"] == "table"
    assert len(csv_out.display["columns"]) == 20
    assert len(csv_out.display["rows"]) == 200
    assert csv_out.display["truncated"] is True

    wb = Workbook()
    ws1 = wb.active
    assert ws1 is not None
    ws1.title = "First"
    ws1.append(["Item", "Qty"])
    ws1.append(["Apples", 4])
    ws2 = wb.create_sheet("Second")
    ws2.append(["Other", "Val"])
    ws2.append(["Oranges", 9])
    wb.save(workspace.root / "two.xlsx")

    xlsx_out = await tool.forward("two.xlsx")
    assert xlsx_out.display == {
        "kind": "table",
        "columns": [
            {"key": "Item", "label": "Item"},
            {"key": "Qty", "label": "Qty"},
        ],
        "rows": [{"Item": "Apples", "Qty": "4"}],
        "truncated": False,
    }

    pdf_fixture = next(DOCS_FIXTURES.glob("*.pdf"))
    shutil.copy(pdf_fixture, workspace.root / "sample.pdf")
    pdf_out = await tool.forward("sample.pdf")
    assert pdf_out.display is None


async def test_tools_without_displays_keep_display_none(
    workspace: Workspace,
    aldwin_home: Path,
    fixture_server: FixtureControls,
    approver: Callable[..., Any],
) -> None:
    """`calculate`, `todo`, `recall`, `read_file`, and MCP tools emit `display=None`."""
    session = PolicySession(workspace=workspace, home=aldwin_home)
    (workspace.root / "hi.txt").write_text("hello\n", encoding="utf-8")

    calc_out = await CalculateTool().forward("2 + 2")
    assert calc_out.display is None

    todo_out = await TodoTool().forward([{"text": "One", "status": "pending"}])
    assert todo_out.display is None

    _, recall_tool, _ = build_memory_tools(home=aldwin_home)
    recall_out = await recall_tool.forward("anything")
    assert recall_out.display is None

    read_out = await ReadFileTool(workspace, session).forward("hi.txt")
    assert read_out.display is None

    cfg = ConnectorConfig(name="fixture", url="https://fixture.example/mcp")

    async def _noop_lost(_e: Any) -> None:
        return None

    async def _noop_changed() -> None:
        return None

    conn = McpConnection(
        cfg,
        auth=None,
        on_tools_changed=_noop_changed,
        on_lost=_noop_lost,
        transport_factory=lambda _http: fixture_server.server,
    )
    await conn.open()
    try:
        spec = RemoteToolSpec(
            name="read_thing",
            title="Read thing",
            description="Read a thing.",
            input_schema={"type": "object", "properties": {}},
            read_only_hint=False,
        )
        pol = connector_policy(cfg, spec)
        assert pol is not None
        register_runtime_policy(pol)
        try:
            mcp_tool = McpTool(
                connector=cfg,
                spec=spec,
                connection=conn,
                tmp_dir=aldwin_home / "tmp",
            )
            expected_detail = mcp_tool.prepare_approval().detail
            asks: list[ApprovalRequest] = []
            wrapped = wrap_tools_with_policy(
                [mcp_tool], session=session, ask=approver("yes", asks=asks)
            )[0]
            session.enqueue_step_call("c_mcp", pol.name, {})
            await wrapped.call({})
            assert len(asks) == 1
            assert asks[0].display is None
            assert asks[0].detail == expected_detail
            assert session.take_display("c_mcp") is None
        finally:
            unregister_runtime_policy(pol.name)
    finally:
        await conn.close()
