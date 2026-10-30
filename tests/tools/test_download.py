"""Unit tests for `download_file`."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from agentling.tools import ToolCallError

from aldwin import __version__
from aldwin.core.approval_log import ApprovalLogger
from aldwin.core.policy import (
    DENIAL_TEXT,
    ApprovalAnswer,
    ApprovalRequest,
    PolicySession,
    decide,
    wrap_tools_with_policy,
)
from aldwin.core.tools.download import (
    DownloadFileTool,
    build_download_tool,
    running_summary,
    sanitize_filename,
)
from aldwin.core.workspace import Workspace


async def _public_resolver(host: str, port: int) -> list[str]:
    del port
    table = {
        "example.com": ["93.184.216.34"],
        "a.example": ["93.184.216.34"],
        "b.example": ["93.184.216.35"],
        "x.example": ["93.184.216.36"],
        "public.example": ["93.184.216.34"],
    }
    if host in table:
        return table[host]
    return ["93.184.216.34"]


async def _call_fail(tool, args: dict) -> str:
    with pytest.raises(ToolCallError) as caught:
        await tool.call(args)
    return str(caught.value)


@pytest.fixture
def download(tmp_path: Path, aldwin_home: Path) -> SimpleNamespace:
    """Workspace, ask spy, and helpers to build a wrapped download tool."""
    root = tmp_path / "ws"
    root.mkdir()
    workspace = Workspace(root, home=aldwin_home)
    session = PolicySession(workspace=workspace, home=aldwin_home)
    log = ApprovalLogger(home=aldwin_home)
    asks: list[ApprovalRequest] = []
    box = {"answer": ApprovalAnswer("yes", "user")}

    async def ask(req: ApprovalRequest) -> ApprovalAnswer:
        asks.append(req)
        return box["answer"]

    def wrap(client: httpx.AsyncClient) -> DownloadFileTool:
        raw = build_download_tool(
            workspace,
            session,
            client=client,
            resolver=_public_resolver,
        )
        wrapped = wrap_tools_with_policy(
            [raw],
            session=session,
            ask=ask,
            approval_log=log,
        )[0]
        return wrapped  # type: ignore[return-value]

    return SimpleNamespace(
        root=root,
        home=aldwin_home,
        workspace=workspace,
        session=session,
        log=log,
        asks=asks,
        answer=box,
        wrap=wrap,
    )


async def test_loopback_ssrf(download: SimpleNamespace) -> None:
    """Loopback URLs are blocked before ask; handler never runs."""
    hit = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal hit
        hit = True
        return httpx.Response(200, content=b"x", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        msg = await _call_fail(tool, {"url": "http://127.0.0.1:8080/a.pdf"})
    assert "blocked: private address" in msg
    assert download.asks == []
    assert hit is False
    assert not (download.root / "downloads").exists() or not any(
        (download.root / "downloads").iterdir()
    )


async def test_redirect_to_link_local(download: SimpleNamespace) -> None:
    """Redirect to link-local is blocked; no leftover files."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public.example":
            return httpx.Response(
                302,
                headers={"Location": "http://169.254.169.254/latest"},
                request=request,
            )
        return httpx.Response(200, content=b"secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        msg = await _call_fail(tool, {"url": "https://public.example/a.pdf"})
    assert "blocked: private address" in msg
    assert not list(download.root.rglob("*.aldwin-tmp"))
    assert not (download.root / "downloads" / "a.pdf").exists()


async def test_hundred_mb_cap(download: SimpleNamespace) -> None:
    """Stream over 100 MB aborts; dest and temps are gone."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "application/octet-stream",
                    "Content-Disposition": 'attachment; filename="big.bin"',
                },
                request=request,
            )
        # Stream just over the cap via a large content body
        payload = b"x" * (100 * 1024 * 1024 + 64)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/octet-stream"},
            content=payload,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        msg = await _call_fail(tool, {"url": "https://example.com/big.bin"})
    assert msg == "too large (limit 100 MB)"
    assert not (download.root / "downloads" / "big.bin").exists()
    assert not list(download.root.rglob("*.aldwin-tmp"))


async def test_happy_download(download: SimpleNamespace) -> None:
    """Approved download saves under downloads/ with FR summary."""
    payload = b"%PDF" + (b"a" * (2 * 1024 * 1024 + 300_000))  # ~2.3 MB

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "Content-Type": "application/pdf",
            "Content-Length": str(len(payload)),
            "Content-Disposition": 'attachment; filename="report.pdf"',
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers, request=request)
        return httpx.Response(200, headers=headers, content=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        result = await tool.call({"url": "https://example.com/report.pdf"})
    assert result.ok is True
    dest = download.root / "downloads" / "report.pdf"
    assert dest.exists() and dest.stat().st_size == len(payload)
    assert "saved downloads/report.pdf" in str(result)
    assert "application/pdf" in str(result)
    assert result.summary.startswith("Download report.pdf · ")
    assert len(download.asks) == 1
    assert "report.pdf" in download.asks[0].summary
    assert download.asks[0].grantable is False


async def test_download_user_agent_matches_installed_version(
    download: SimpleNamespace,
) -> None:
    """HEAD and GET both advertise the installed version."""
    payload = b"%PDF-1.4 ua"
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["User-Agent"])
        headers = {
            "Content-Type": "application/pdf",
            "Content-Length": str(len(payload)),
            "Content-Disposition": 'attachment; filename="ua.pdf"',
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers, request=request)
        return httpx.Response(200, headers=headers, content=payload, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        result = await tool.call({"url": "https://example.com/ua.pdf"})

    assert result.ok is True
    assert seen
    expected = f"Aldwin/{__version__} (+https://askaldwin.com)"
    assert all(header == expected for header in seen)


async def test_no_overwrite(download: SimpleNamespace) -> None:
    """Existing dest soft-fails before ask and leaves bytes unchanged."""
    dest_dir = download.root / "downloads"
    dest_dir.mkdir()
    existing = dest_dir / "report.pdf"
    existing.write_bytes(b"keep-me")

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="report.pdf"',
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers, request=request)
        return httpx.Response(200, headers=headers, content=b"new", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        msg = await _call_fail(tool, {"url": "https://example.com/report.pdf"})
    assert (
        msg
        == "downloads/report.pdf already exists; pass dest to save under another name"
    )
    assert existing.read_bytes() == b"keep-me"
    assert download.asks == []


async def test_cancel_mid_download(download: SimpleNamespace) -> None:
    """Cancel mid-stream removes temp and dest."""

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Disposition": 'attachment; filename="slow.bin"',
            "Content-Length": str(2 * 1024 * 1024),
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers, request=request)
        return httpx.Response(
            200,
            headers=headers,
            content=b"y" * (2 * 1024 * 1024),
            request=request,
        )

    cancel_box: dict[str, asyncio.Task[object] | None] = {"task": None}

    async def on_progress(written: int, total: int | None) -> None:
        del total
        task = cancel_box["task"]
        if task is not None and written > 0:
            task.cancel()

    async def ask(req: ApprovalRequest) -> ApprovalAnswer:
        download.asks.append(req)
        return ApprovalAnswer("yes", "user")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        raw = build_download_tool(
            download.workspace,
            download.session,
            client=client,
            resolver=_public_resolver,
            on_progress=on_progress,
        )
        wrapped = wrap_tools_with_policy(
            [raw],
            session=download.session,
            ask=ask,
            approval_log=download.log,
        )[0]
        task = asyncio.create_task(
            wrapped.call({"url": "https://example.com/slow.bin"})
        )
        cancel_box["task"] = task
        with pytest.raises(asyncio.CancelledError):
            await task

    await asyncio.sleep(0.05)
    assert not (download.root / "downloads" / "slow.bin").exists()
    assert not list(download.root.rglob("*.aldwin-tmp"))


async def test_disposition_id_rsa_blocked(download: SimpleNamespace) -> None:
    """Blocked filename from Content-Disposition fails before ask."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": 'attachment; filename="id_rsa"',
            },
            content=b"key",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        msg = await _call_fail(tool, {"url": "https://example.com/key"})
    assert "blocked" in msg.lower() and "secret" in msg.lower()
    assert download.asks == []
    assert not (download.root / "downloads" / "id_rsa").exists()


async def test_redirect_host_summary(download: SimpleNamespace) -> None:
    """Cross-host redirect appears in the approval summary and detail."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.example":
            return httpx.Response(
                302,
                headers={"Location": "https://b.example/file.pdf"},
                request=request,
            )
        headers = {
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="file.pdf"',
            "Content-Length": "4",
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers, request=request)
        return httpx.Response(200, headers=headers, content=b"%PDF", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        result = await tool.call({"url": "https://a.example/start"})
    assert result.ok is True
    assert len(download.asks) >= 1
    summary = download.asks[0].summary
    assert "from b.example (redirected from a.example)" in summary
    assert "https://a.example/start" in (download.asks[0].detail or "")
    assert "https://b.example/file.pdf" in (download.asks[0].detail or "")
    assert download.asks[0].grantable is False


async def test_program_file_risky(download: SimpleNamespace) -> None:
    """Program extensions get the risky label and won't-run suffix."""

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "Content-Type": "application/x-sh",
            "Content-Disposition": 'attachment; filename="setup.sh"',
            "Content-Length": "2",
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers, request=request)
        return httpx.Response(200, headers=headers, content=b"#!", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        result = await tool.call({"url": "https://example.com/setup.sh"})
    assert result.ok is True
    assert download.asks[0].risky == ("program file",)
    assert download.asks[0].summary.endswith("· Aldwin won't run it?")


async def test_log_redacts_token(download: SimpleNamespace) -> None:
    """Approvals log redacts query secrets and never stores PDF bytes."""

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="r.pdf"',
            "Content-Length": "4",
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers, request=request)
        return httpx.Response(200, headers=headers, content=b"%PDF", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        await tool.call({"url": "https://x.example/r.pdf?token=abc"})
    log_text = (download.home / "logs" / "approvals.jsonl").read_text(encoding="utf-8")
    assert "token=‹redacted›" in log_text or "‹redacted›" in log_text
    assert "token=abc" not in log_text
    assert "%PDF" not in log_text


async def test_user_declines(download: SimpleNamespace) -> None:
    """Decline returns the fixed user-denial text and saves nothing."""
    download.answer["answer"] = ApprovalAnswer("no", "user")

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="n.pdf"',
            "Content-Length": "4",
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers, request=request)
        return httpx.Response(200, headers=headers, content=b"%PDF", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        msg = await _call_fail(tool, {"url": "https://example.com/n.pdf"})
    assert msg.startswith(DENIAL_TEXT["user"].split(".")[0])
    assert "Denied by the user" in msg
    assert not (download.root / "downloads" / "n.pdf").exists()
    assert download.session.turn_denials >= 1


async def test_get_host_drift(download: SimpleNamespace) -> None:
    """GET redirect to a new host after approve fails the safety check."""

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="d.pdf"',
            "Content-Length": "4",
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers, request=request)
        if request.url.host == "a.example":
            return httpx.Response(
                302,
                headers={"Location": "https://b.example/d.pdf"},
                request=request,
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "application/pdf"},
            content=b"%PDF",
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        msg = await _call_fail(tool, {"url": "https://a.example/d.pdf"})
    assert "safety check failed" in msg.lower() or DENIAL_TEXT["policy_error"] in msg
    assert not (download.root / "downloads" / "d.pdf").exists()


async def test_optional_dest_decide(download: SimpleNamespace) -> None:
    """Missing dest does not fail closed in decide(); prepare picks downloads/."""
    decision = decide(
        "download_file",
        {"url": "https://example.com/a.pdf"},
        download.session,
    )
    assert decision.kind == "ask"

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="a.pdf"',
            "Content-Length": "4",
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers, request=request)
        return httpx.Response(200, headers=headers, content=b"%PDF", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        result = await tool.call({"url": "https://example.com/a.pdf"})
    assert result.ok is True
    assert (download.root / "downloads" / "a.pdf").exists()


async def test_non_http_schemes(download: SimpleNamespace) -> None:
    """file:// and ftp:// are refused without asking."""
    hit = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal hit
        hit = True
        return httpx.Response(200, content=b"x", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        for bad in ("file:///etc/passwd", "ftp://example.com/a"):
            msg = await _call_fail(tool, {"url": bad})
            lower = msg.lower()
            assert "http" in lower or "refused" in lower or "blocked" in lower
    assert hit is False
    assert download.asks == []


async def test_content_length_over_cap(download: SimpleNamespace) -> None:
    """HEAD Content-Length over 100 MB fails before saving."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": "120000000",
                "Content-Disposition": 'attachment; filename="big.bin"',
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        msg = await _call_fail(tool, {"url": "https://example.com/big.bin"})
    assert msg == "too large (limit 100 MB)"
    assert not (download.root / "downloads" / "big.bin").exists()


def test_filename_sanitise() -> None:
    """Path traversal and leading dots are stripped from names."""
    assert "/" not in sanitize_filename("../../evil.pdf")
    assert "\\" not in sanitize_filename("..\\evil.pdf")
    assert not sanitize_filename(".hidden").startswith(".")
    assert "\x00" not in sanitize_filename("a\x00b.pdf")
    assert sanitize_filename("../../evil.pdf") == "evil.pdf"


async def test_default_download_name(download: SimpleNamespace) -> None:
    """Empty path and no disposition falls back to download-YYYYMMDD-HHMMSS."""

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Length": "3",
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers, request=request)
        return httpx.Response(200, headers=headers, content=b"abc", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        result = await tool.call({"url": "https://example.com/"})
    assert result.ok is True
    files = list((download.root / "downloads").iterdir())
    assert len(files) == 1
    assert files[0].name.startswith("download-")


async def test_digest_binds_urls_and_dest(download: SimpleNamespace) -> None:
    """Different final URLs produce different digests."""
    from aldwin.core.policy import compute_digest

    d1 = compute_digest(
        "download_file",
        {"url": "https://a.example/x", "dest": "downloads/a.pdf"},
        "url=https://a.example/x\nfinal_url=https://a.example/x\ndest=downloads/a.pdf",
    )
    d2 = compute_digest(
        "download_file",
        {"url": "https://a.example/x", "dest": "downloads/a.pdf"},
        "url=https://a.example/x\nfinal_url=https://b.example/x\ndest=downloads/a.pdf",
    )
    assert d1 != d2


async def test_dest_outside_workspace_never_grantable(
    download: SimpleNamespace, tmp_path: Path
) -> None:
    """Absolute dest outside the workspace asks and is never grantable."""
    outside = tmp_path / "outside_dir"
    outside.mkdir()
    dest = outside / "report.pdf"

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {
            "Content-Type": "application/pdf",
            "Content-Disposition": 'attachment; filename="report.pdf"',
            "Content-Length": "4",
        }
        if request.method == "HEAD":
            return httpx.Response(200, headers=headers, request=request)
        return httpx.Response(200, headers=headers, content=b"%PDF", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download.wrap(client)
        result = await tool.call(
            {"url": "https://example.com/report.pdf", "dest": str(dest)}
        )
    assert result.ok is True
    assert dest.exists()
    assert len(download.asks) >= 1
    assert download.asks[0].grantable is False


def test_running_summary_download(download: SimpleNamespace) -> None:
    """Pending row prefers dest basename, else URL path name."""
    assert (
        running_summary(
            "download_file",
            {"url": "https://example.com/a.pdf", "dest": "downloads/x.pdf"},
            download.workspace,
        )
        == "Download x.pdf"
    )
    assert (
        running_summary(
            "download_file",
            {"url": "https://example.com/report.pdf"},
            download.workspace,
        )
        == "Download report.pdf"
    )
