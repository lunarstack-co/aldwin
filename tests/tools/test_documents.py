"""Unit tests for `read_document`."""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from agentling.tools import ToolCallError

from aldwin.core.agent import SessionContext, build_tools, load_base_instructions
from aldwin.core.approval_log import ApprovalLogger
from aldwin.core.policy import (
    DEFAULT_MAX_OUTPUT_CHARS_BY_TOOL,
    ApprovalAnswer,
    ApprovalRequest,
    PolicySession,
    wrap_tools_with_policy,
)
from aldwin.core.tools import list_builtin_tools
from aldwin.core.tools.documents import (
    ReadDocumentTool,
    build_document_tools,
    convert_document,
    running_summary,
)
from aldwin.core.tools.download import DownloadFileTool
from aldwin.core.workspace import Workspace

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "docs"


async def _call_fail(tool, args: dict) -> str:
    with pytest.raises(ToolCallError) as caught:
        await tool.call(args)
    return str(caught.value)


def _inner(text: str) -> str:
    match = re.search(
        r'<untrusted id="u-[0-9a-f]+" source="[^"]*">\n'
        r"(?P<body>.*)\n"
        r'</untrusted id="u-[0-9a-f]+">',
        text,
        re.DOTALL,
    )
    assert match is not None, text
    return match.group("body")


@pytest.fixture
def docs(tmp_path: Path, aldwin_home: Path) -> SimpleNamespace:
    """Workspace with document fixtures and wrapped `read_document`."""
    root = tmp_path / "ws"
    root.mkdir()
    for name in FIXTURES.iterdir():
        if name.is_file():
            shutil.copy(name, root / name.name)
    workspace = Workspace(root, home=aldwin_home)
    session = PolicySession(workspace=workspace, home=aldwin_home)
    log = ApprovalLogger(home=aldwin_home)
    asks: list[ApprovalRequest] = []
    box = {"answer": ApprovalAnswer("yes", "user")}

    async def ask(req: ApprovalRequest) -> ApprovalAnswer:
        asks.append(req)
        return box["answer"]

    wrapped = wrap_tools_with_policy(
        build_document_tools(workspace, session),
        session=session,
        ask=ask,
        approval_log=log,
    )
    return SimpleNamespace(
        root=root,
        home=aldwin_home,
        workspace=workspace,
        session=session,
        log=log,
        asks=asks,
        answer=box,
        tools={tool.name: tool for tool in wrapped},
    )


async def test_text_pdf_two_pages(docs: SimpleNamespace) -> None:
    """Text PDF yields page headings, words in the summary, and no ask."""
    result = await docs.tools["read_document"].call({"path": "bill.pdf"})
    assert result.ok is True
    body = _inner(str(result))
    assert "## Page 1" in body and "## Page 2" in body
    assert "Energy bill" in body
    assert result.summary.startswith("Read bill.pdf · pages 1–2 · ")
    assert "words" in result.summary
    assert docs.asks == []


async def test_scanned_pdf_ocr_note(docs: SimpleNamespace) -> None:
    """Scanned page (little text + image) gets the OCR note."""
    result = await docs.tools["read_document"].call({"path": "scanned.pdf"})
    body = _inner(str(result))
    assert "## Page 1" in body
    assert "(no text layer: scanned page; OCR arrives in 0.0.3)" in body
    assert result.summary.endswith("· 0 words")


async def test_blank_pdf_no_ocr_note(docs: SimpleNamespace) -> None:
    """A blank page with no image must not get the scanned OCR message."""
    result = await docs.tools["read_document"].call({"path": "blank.pdf"})
    body = _inner(str(result))
    assert "## Page 1" in body
    assert "OCR arrives" not in body


async def test_pages_selection_out_of_range(docs: SimpleNamespace) -> None:
    """Page ranges select content; out-of-range pages become notes."""
    result = await docs.tools["read_document"].call(
        {"path": "five_pages.pdf", "pages": "2-3,9"}
    )
    body = _inner(str(result))
    assert "## Page 2" in body and "## Page 3" in body
    assert "## Page 1" not in body
    assert "page 9 does not exist (5 pages)" in body


async def test_encrypted_pdf_message(docs: SimpleNamespace) -> None:
    """Encrypted PDF soft-fails with the unlock message."""
    msg = await _call_fail(docs.tools["read_document"], {"path": "secret.pdf"})
    assert msg == "encrypted: secret.pdf; open it and save an unlocked copy"
    assert docs.asks == []


async def test_xlsx_sheet_row_cap(docs: SimpleNamespace) -> None:
    """XLSX sheet listing, 200-row cap, and summary row counts."""
    result = await docs.tools["read_document"].call(
        {"path": "Q3_large.xlsx", "sheet": "Q3 Detail"}
    )
    body = _inner(str(result))
    assert body.startswith("Sheets: Summary, Q3 Detail, Notes")
    assert "… 1,340 more rows" in body
    assert result.summary == "Read Q3_large.xlsx · sheet Q3 Detail · 200 of 1,540 rows"


async def test_unsupported_type_lists_formats(docs: SimpleNamespace) -> None:
    """Unsupported extension lists the supported types."""
    msg = await _call_fail(docs.tools["read_document"], {"path": "x.pages"})
    assert (
        msg
        == "unsupported type .pages; supported: pdf, docx, pptx, xlsx, csv, tsv, html"
    )


async def test_injection_pdf_hardline_blocks_rm(docs: SimpleNamespace) -> None:
    """Injected PDF text is wrapped once; hardline still blocks rm -rf."""
    from aldwin.core.policy import match_hardline, normalise_command
    from aldwin.core.tools.run_command import build_run_command_tool

    result = await docs.tools["read_document"].call({"path": "inject.pdf"})
    text = str(result)
    openings = re.findall(r'<untrusted id="u-[0-9a-f]{8}"', text)
    assert len(openings) == 1
    inner = _inner(text).lower()
    assert "rm -rf" in inner or "ignore previous" in inner

    async def ask(req: ApprovalRequest) -> ApprovalAnswer:
        docs.asks.append(req)
        return ApprovalAnswer("yes", "user")

    async def legacy_ask(_summary: str, _detail: str = "") -> bool:
        return True

    cmd = wrap_tools_with_policy(
        [
            build_run_command_tool(
                ask=legacy_ask,
                cwd=docs.root,
                wrapped_by_policy=True,
            )
        ],
        session=docs.session,
        ask=ask,
    )[0]
    before = len(docs.asks)
    assert match_hardline(normalise_command("rm -rf ~")) is not None
    denied = await _call_fail(cmd, {"command": "rm -rf ~"})
    assert "never allowed" in denied.lower() or "blocked" in denied.lower()
    assert len(docs.asks) == before


async def test_forged_untrusted_tag_in_pdf_is_escaped(docs: SimpleNamespace) -> None:
    """A forged closing untrusted tag inside the PDF is escaped once."""
    result = await docs.tools["read_document"].call({"path": "escape_untrusted.pdf"})
    text = str(result)
    assert text.count("<untrusted ") == 1
    assert "&lt;/untrusted" in text
    body = _inner(text)
    assert re.search(r'</untrusted id="u-0000">', body) is None
    assert "u-0000" in body or "forged close" in body


async def test_document_too_large(
    docs: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files over 50 MB soft-fail before conversion."""
    monkeypatch.setattr("aldwin.core.tools.documents.MAX_DOCUMENT_BYTES", 10)
    msg = await _call_fail(docs.tools["read_document"], {"path": "bill.pdf"})
    assert "too large:" in msg and "limit 50 MB" in msg


async def test_sheet_default_and_index(docs: SimpleNamespace) -> None:
    """Default sheet is first; numeric sheet selects by 1-based index."""
    default = await docs.tools["read_document"].call({"path": "Q3_large.xlsx"})
    body = _inner(str(default))
    assert body.startswith("Sheets: Summary, Q3 Detail, Notes")
    assert "sheet Summary" in default.summary

    second = await docs.tools["read_document"].call(
        {"path": "Q3_large.xlsx", "sheet": "2"}
    )
    assert "sheet Q3 Detail" in second.summary


async def test_docx_pptx_shape(docs: SimpleNamespace) -> None:
    """DOCX and PPTX convert to non-empty markdown with slide headings."""
    docx = await docs.tools["read_document"].call({"path": "contract.docx"})
    assert docx.ok is True
    assert _inner(str(docx)).strip()

    pptx = await docs.tools["read_document"].call({"path": "deck.pptx"})
    assert pptx.ok is True
    body = _inner(str(pptx))
    assert "## Slide 1" in body


async def test_csv_semicolon_latin1(docs: SimpleNamespace) -> None:
    """CSV with `;` and latin-1 bytes parses without crashing."""
    result = await docs.tools["read_document"].call({"path": "semi.csv"})
    assert result.ok is True
    body = _inner(str(result))
    assert "|" in body


async def test_html_markdownify_keeps_links(docs: SimpleNamespace) -> None:
    """HTML keeps links and strips nav/script noise."""
    result = await docs.tools["read_document"].call({"path": "page.html"})
    assert result.ok is True
    body = _inner(str(result))
    assert "<script" not in body.lower()
    assert body.strip()


async def test_output_caps() -> None:
    """Tool and DEFAULT_MAX caps match 30k / 1k."""
    assert ReadDocumentTool.max_output_chars == 30_000
    assert DownloadFileTool.max_output_chars == 1_000
    assert DEFAULT_MAX_OUTPUT_CHARS_BY_TOOL["read_document"] == 30_000
    assert DEFAULT_MAX_OUTPUT_CHARS_BY_TOOL["download_file"] == 1_000


async def test_pdf_page_footer_when_truncated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the char budget fills, remaining pages get a pages= footer."""
    monkeypatch.setattr("aldwin.core.tools.documents.MAX_DOCUMENT_CHARS", 80)
    body, _suffix = convert_document(FIXTURES / "five_pages.pdf")
    assert "not shown; call again with pages=" in body


async def test_outside_document_ask_once(docs: SimpleNamespace, tmp_path: Path) -> None:
    """Outside-workspace PDF asks once; yes_session skips the second ask."""
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.copy(FIXTURES / "bill.pdf", outside / "bill.pdf")
    docs.answer["answer"] = ApprovalAnswer("yes_session", "user")
    result = await docs.tools["read_document"].call({"path": str(outside / "bill.pdf")})
    assert result.ok is True
    assert len(docs.asks) == 1
    docs.asks.clear()
    docs.answer["answer"] = ApprovalAnswer("yes", "user")
    again = await docs.tools["read_document"].call({"path": str(outside / "bill.pdf")})
    assert again.ok is True
    assert docs.asks == []


async def test_read_document_auto_log_has_no_body(docs: SimpleNamespace) -> None:
    """Auto log records path, never page body."""
    await docs.tools["read_document"].call({"path": "bill.pdf"})
    log_path = docs.home / "logs" / "approvals.jsonl"
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert lines
    last = lines[-1]
    assert "read_document" in last
    assert "auto" in last
    assert "Energy bill" not in last


async def test_single_untrusted_wrapper(docs: SimpleNamespace) -> None:
    """Tool source has no untrusted wrapper; policy wraps once."""
    result = await docs.tools["read_document"].call({"path": "bill.pdf"})
    assert str(result).count("<untrusted") == 1
    source = Path("src/aldwin/core/tools/documents.py").read_text(encoding="utf-8")
    assert "<untrusted" not in source


async def test_cancel_mid_pdf() -> None:
    """Cancel between PDF pages raises CancelledError."""
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(asyncio.CancelledError):
        convert_document(FIXTURES / "five_pages.pdf", cancel=cancel)


async def test_registration_and_catalog(
    docs: SimpleNamespace, aldwin_home: Path
) -> None:
    """Both tools register; catalog and instructions mention them."""
    ctx = SessionContext(
        workspace=docs.workspace,
        policy_session=docs.session,
        home=aldwin_home,
    )
    built = {tool.name: tool for tool in build_tools(ctx)}
    assert "read_document" in built
    assert "download_file" in built
    catalog = {item.name: item for item in list_builtin_tools()}
    assert catalog["read_document"].requires_approval is False
    assert catalog["download_file"].requires_approval is True
    instructions = load_base_instructions()
    assert "read_document" in instructions and "download_file" in instructions
    for path in Path("src/aldwin/cli").rglob("*.py"):
        assert "aldwin.core" not in path.read_text(encoding="utf-8")


async def test_xlsx_missing_formula_cache(docs: SimpleNamespace) -> None:
    """Missing formula cache shows the placeholder, not a blank."""
    from openpyxl import Workbook

    path = docs.root / "formulas.xlsx"
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Calc"
    ws["A1"] = "n"
    ws["A2"] = 1
    ws["B2"] = "=A2*2"
    wb.save(path)
    result = await docs.tools["read_document"].call({"path": "formulas.xlsx"})
    body = _inner(str(result))
    assert "(formula, not calculated)" in body


async def test_bad_sheet_name_lists_sheets(docs: SimpleNamespace) -> None:
    """Unknown sheet soft-fails listing available sheets."""
    msg = await _call_fail(
        docs.tools["read_document"],
        {"path": "Q3_large.xlsx", "sheet": "Nope"},
    )
    assert "Nope" in msg and "Sheets:" in msg


def test_running_summary_read_document(docs: SimpleNamespace) -> None:
    """Pending row for read_document uses the display path."""
    assert (
        running_summary("read_document", {"path": "bill.pdf"}, docs.workspace)
        == "Read bill.pdf"
    )


def test_markitdown_not_a_dependency() -> None:
    """Spike outcome: MarkItDown is not installed."""
    import importlib

    with pytest.raises(ImportError):
        importlib.import_module("markitdown")


def test_read_docx_without_office_extra_gives_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing mammoth raises a ToolCallError pointing at `aldwin[office]`."""
    import sys

    monkeypatch.setitem(sys.modules, "mammoth", None)
    with pytest.raises(ToolCallError, match=r"aldwin\[office\]"):
        convert_document(FIXTURES / "contract.docx")


def test_read_pptx_without_office_extra_gives_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing python-pptx raises a ToolCallError pointing at `aldwin[office]`."""
    import sys

    monkeypatch.setitem(sys.modules, "pptx", None)
    with pytest.raises(ToolCallError, match=r"aldwin\[office\]"):
        convert_document(FIXTURES / "deck.pptx")


def test_read_xlsx_without_office_extra_gives_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing openpyxl raises a ToolCallError pointing at `aldwin[office]`."""
    import sys

    monkeypatch.setitem(sys.modules, "openpyxl", None)
    with pytest.raises(ToolCallError, match=r"aldwin\[office\]"):
        convert_document(FIXTURES / "Q3_large.xlsx")
