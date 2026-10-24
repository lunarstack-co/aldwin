"""Performance budgets (Linux CI; `@pytest.mark.perf`)."""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import pytest
from perf_slack import budget
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from aldwin.cli.skill_completer import SkillCache, SkillCompleter
from aldwin.core.approval_log import ApprovalLogger
from aldwin.core.memory import remember_note
from aldwin.core.policy import (
    ApprovalAnswer,
    ApprovalRequest,
    PolicySession,
    wrap_tools_with_policy,
)
from aldwin.core.skills import build_catalogue
from aldwin.core.tools.documents import build_document_tools
from aldwin.core.tools.files import build_file_tools
from aldwin.core.tools.memory_tools import RecallTool
from aldwin.core.workspace import Workspace
from aldwin.protocol import SkillCatalogItem

pytestmark = pytest.mark.perf


def _linux_only() -> None:
    if sys.platform != "linux":
        pytest.skip("Perf budgets run on Linux CI only")


def _write_text_pdf(path: Path, pages: int) -> None:
    """Write a minimal multi-page text PDF without extra deps."""
    content_bodies: list[tuple[int, bytes]] = []
    page_obj_nums: list[int] = []
    content_obj_nums: list[int] = []
    obj_num = 3
    for i in range(pages):
        text = f"BT /F1 12 Tf 72 720 Td (Page {i + 1} budget) Tj ET"
        stream = text.encode("latin-1")
        content_bodies.append(
            (
                obj_num,
                (f"<< /Length {len(stream)} >>\nstream\n{text}\nendstream").encode(
                    "latin-1"
                ),
            )
        )
        content_obj_nums.append(obj_num)
        obj_num += 1
        page_obj_nums.append(obj_num)
        obj_num += 1
    page_bodies: list[tuple[int, bytes]] = []
    for pid, cid in zip(page_obj_nums, content_obj_nums, strict=True):
        page_bodies.append(
            (
                pid,
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Contents {cid} 0 R /Resources << /Font << /F1 << /Type /Font "
                    f"/Subtype /Type1 /BaseFont /Helvetica >> >> >> >>"
                ).encode("latin-1"),
            )
        )
    kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
    by_num: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode("latin-1"),
    }
    for n, body in content_bodies:
        by_num[n] = body
    for n, body in page_bodies:
        by_num[n] = body
    last = max(by_num)
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for n in range(1, last + 1):
        offsets[n] = len(out)
        out.extend(f"{n} 0 obj\n".encode("ascii"))
        out.extend(by_num[n])
        out.extend(b"\nendobj\n")
    xref_pos = len(out)
    out.extend(f"xref\n0 {last + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for n in range(1, last + 1):
        out.extend(f"{offsets[n]:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer\n<< /Size {last + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(bytes(out))


@pytest.fixture
def perf_ws(tmp_path: Path, aldwin_home: Path) -> tuple[Workspace, PolicySession]:
    root = tmp_path / "ws"
    root.mkdir()
    ws = Workspace(root, home=aldwin_home)
    return ws, PolicySession(workspace=ws, home=aldwin_home)


async def test_grep_budget_100k_lines(perf_ws, aldwin_home: Path) -> None:
    """`grep` on a generated 100k-line repo: ≤2s with rg, ≤10s without."""
    _linux_only()
    ws, session = perf_ws
    repo = ws.root / "big"
    repo.mkdir()
    for i in range(100):
        (repo / f"f{i}.txt").write_text("needle\n" + ("x\n" * 1000), encoding="utf-8")

    async def ask(_req: ApprovalRequest) -> ApprovalAnswer:
        return ApprovalAnswer("yes", "user")

    tools = {
        t.name: t
        for t in wrap_tools_with_policy(
            build_file_tools(ws, session),
            session=session,
            ask=ask,
            approval_log=ApprovalLogger(home=aldwin_home),
        )
    }
    t0 = time.perf_counter()
    await tools["grep"].call({"pattern": "needle", "path": "big"})
    elapsed = time.perf_counter() - t0
    limit = budget(2.0 if shutil.which("rg") else 10.0)
    assert elapsed < limit, elapsed


async def test_read_document_50_page_pdf(perf_ws, aldwin_home: Path) -> None:
    """`read_document` on a 50-page text PDF: ≤3s."""
    _linux_only()
    ws, session = perf_ws
    pdf = ws.root / "fifty.pdf"
    _write_text_pdf(pdf, 50)

    async def ask(_req: ApprovalRequest) -> ApprovalAnswer:
        return ApprovalAnswer("yes", "user")

    tools = {
        t.name: t
        for t in wrap_tools_with_policy(
            build_document_tools(ws, session),
            session=session,
            ask=ask,
            approval_log=ApprovalLogger(home=aldwin_home),
        )
    }
    t0 = time.perf_counter()
    await tools["read_document"].call({"path": "fifty.pdf"})
    elapsed = time.perf_counter() - t0
    assert elapsed < budget(3.0), elapsed


async def test_recall_budget_after_index(aldwin_home: Path) -> None:
    """`recall` on 3,650 notes: ≤50ms after the index exists."""
    _linux_only()
    (aldwin_home / "memory" / "notes").mkdir(parents=True, exist_ok=True)
    for i in range(3650):
        remember_note(home=aldwin_home, text=f"fact number {i} about testing budgets")
    tool = RecallTool(home=aldwin_home)
    await tool.forward(query="fact", scope="notes")
    t0 = time.perf_counter()
    await tool.forward(query="fact 42", scope="notes")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < budget(0.050) * 1000, elapsed_ms


def test_skill_catalogue_budget(aldwin_home: Path) -> None:
    """Skill catalogue build (defaults + 50 user): ≤100ms."""
    _linux_only()
    skills = aldwin_home / "skills"
    skills.mkdir(exist_ok=True)
    for i in range(50):
        folder = skills / f"user-skill-{i}"
        folder.mkdir()
        (folder / "SKILL.md").write_text(
            f"---\nname: user-skill-{i}\n"
            f"description: User skill {i} Load when testing.\n---\n\nBody.\n",
            encoding="utf-8",
        )
    t0 = time.perf_counter()
    cat = build_catalogue(aldwin_home, version="0.0.2", model="gpt-test")
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert len(cat) >= 50
    assert elapsed_ms < budget(0.100) * 1000, elapsed_ms


def test_at_filter_budget() -> None:
    """`@` list filter with 60 skills: ≤16ms per keystroke."""
    _linux_only()
    items = [
        SkillCatalogItem(
            name=f"skill-{i:02d}",
            description=f"Description for skill {i}",
            source="user",
            flags=[],
            last_used=None,
        )
        for i in range(60)
    ]
    completer = SkillCompleter(SkillCache(items=items))
    doc = Document("@ski", cursor_position=4)
    event = CompleteEvent(completion_requested=True)
    t0 = time.perf_counter()
    list(completer.get_completions(doc, event))
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert elapsed_ms < budget(0.016) * 1000, elapsed_ms
