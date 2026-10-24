"""Six OpenAI launch demos via pexpect. Skip without OPENAI_API_KEY."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest
from pexpect.exceptions import TIMEOUT

from e2e.harness import AldwinSpawn, write_transcript
from e2e.http_stub import start_pdf_stub
from e2e.workspace_builder import (
    DECK_MARKER,
    SHEET2_MARKER,
    build_e2e_workspace,
    seed_memory_note,
)

pytestmark = pytest.mark.e2e

DOCS = Path(__file__).resolve().parents[1] / "fixtures" / "docs"

DEMOS: dict[str, str] = {
    "01_todos": "Find the TODOs in src/ and fix them.",
    "02_bills": "Total my bills by month into bills.csv under the workspace root.",
    "03_sheet_deck": "Summarise sheet 2 of Q3.xlsx and the deck.pptx.",
    "04_download": "DOWNLOAD_URL",
    "05_memory": "I moved to Chelmsford. Update my address note.",
    "06_skill": (
        "Make a skill named weekly-report that writes reports/week-01.md "
        "with a one-line summary of this workspace."
    ),
}


def _require_runtime() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        pytest.skip("OPENAI_API_KEY is unset; skipping e2e")
    if shutil.which("aldwin") is None:
        pytest.skip("aldwin binary not on PATH; install with `uv tool install -e .`")
    return key


def _prepare(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "aldwin-home"
    home.mkdir()
    (home / "config.yaml").write_text(
        "model: gpt-4.1-mini\nprovider: openai\n",
        encoding="utf-8",
    )
    seed_memory_note(home)
    ws = build_e2e_workspace(tmp_path / "workspace")
    return home, ws


def _drive(
    spawn: AldwinSpawn,
    prompt: str,
    *,
    approvals: int = 8,
    timeout: float = 180,
) -> str:
    """Send a prompt and auto-answer approvals with `y` until the turn settles."""
    spawn.child.expect(r"› |You:|> ", timeout=60)
    spawn.send_line(prompt)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and approvals > 0:
        idx = spawn.child.expect(
            [
                r"\[y/a/N\]|Allow|Approve|Run this|Yes,",
                r"› |You:|> |\(stopped\)|✓ done|done ·",
                spawn._pexpect.TIMEOUT,
            ],
            timeout=min(30, deadline - time.monotonic()),
        )
        if idx == 0:
            spawn.answer("y")
            approvals -= 1
            continue
        if idx == 1:
            break
    return spawn.wait_done(timeout=30)


@pytest.mark.parametrize("demo", sorted(DEMOS))
def test_demo(demo: str, tmp_path: Path) -> None:
    """Each launch demo asserts file outcomes when a key is present (one retry)."""
    key = _require_runtime()
    home, ws = _prepare(tmp_path)
    prompt = DEMOS[demo]
    server = None
    if demo == "04_download":
        pdf = DOCS / "five_pages.pdf"
        if not pdf.is_file():
            pdf = DOCS / "bill.pdf"
        server, base = start_pdf_stub(pdf)
        prompt = f"Download {base}/report.pdf and give me 3 points from it."
    try:
        last_err: BaseException | None = None
        for attempt in range(2):
            attempt_home = home if attempt == 0 else tmp_path / f"home-{attempt}"
            attempt_ws = (
                ws if attempt == 0 else build_e2e_workspace(tmp_path / f"ws-{attempt}")
            )
            if attempt > 0:
                attempt_home.mkdir(exist_ok=True)
                (attempt_home / "config.yaml").write_text(
                    "model: gpt-4.1-mini\nprovider: openai\n",
                    encoding="utf-8",
                )
                seed_memory_note(attempt_home)
            spawn = AldwinSpawn(attempt_home, attempt_ws, env={"OPENAI_API_KEY": key})
            try:
                text = _drive(spawn, prompt)
                path = write_transcript(f"{demo}_attempt{attempt}", text)
                _assert_demo(demo, attempt_ws, attempt_home, text)
                assert path.is_file()
                return
            except (TIMEOUT, OSError, AssertionError) as exc:
                # Retry once on timeout / model variance; fail fast on other errors.
                if isinstance(exc, AssertionError) and attempt == 1:
                    raise
                if isinstance(exc, AssertionError):
                    last_err = exc
                    continue
                last_err = exc
            finally:
                spawn.close()
        assert last_err is None, last_err
    finally:
        if server is not None:
            server.shutdown()


def _assert_demo(demo: str, ws: Path, home: Path, text: str) -> None:
    if demo == "01_todos":
        for name in ("a.py", "b.py", "c.py"):
            body = (ws / "src" / name).read_text(encoding="utf-8")
            assert "TODO" not in body, body
    elif demo == "02_bills":
        csv_path = ws / "bills.csv"
        assert csv_path.is_file(), text[-2000:]
        rows = [
            ln for ln in csv_path.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        assert len(rows) >= 4
    elif demo == "03_sheet_deck":
        assert SHEET2_MARKER in text or "4242" in text, text[-2000:]
        assert DECK_MARKER in text, text[-2000:]
    elif demo == "04_download":
        downloads = list((ws / "downloads").glob("*.pdf"))
        assert downloads, "expected a PDF under downloads/"
    elif demo == "05_memory":
        notes = home / "memory" / "notes"
        bodies = "\n".join(
            p.read_text(encoding="utf-8") for p in sorted(notes.glob("*.md"))
        )
        assert "chelmsford" in bodies.lower(), bodies
    elif demo == "06_skill":
        skill = home / "skills" / "weekly-report" / "SKILL.md"
        assert skill.is_file(), skill
