"""Download SSRF refusals, 100 MB abort, and no overwrite."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from aldwin.core.policy import ApprovalAnswer, ApprovalRequest, wrap_tools_with_policy
from aldwin.core.tools.download import build_download_tool
from security._helpers import call_fail


async def _public_resolver(host: str, port: int) -> list[str]:
    del port
    return ["93.184.216.34"]


@pytest.fixture
def download_wrap(security_tools: SimpleNamespace):
    """Build a download tool with an injectable httpx client + public resolver."""
    asks: list[ApprovalRequest] = []

    async def ask(req: ApprovalRequest) -> ApprovalAnswer:
        asks.append(req)
        return security_tools.answer["answer"]

    def wrap(client: httpx.AsyncClient):
        raw = build_download_tool(
            security_tools.workspace,
            security_tools.session,
            client=client,
            resolver=_public_resolver,
        )
        wrapped = wrap_tools_with_policy(
            [raw],
            session=security_tools.session,
            ask=ask,
            approval_log=security_tools.log,
        )[0]
        return wrapped

    return SimpleNamespace(wrap=wrap, asks=asks, tools=security_tools)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://10.0.0.1/",
        "file:///etc/passwd",
    ],
)
async def test_ssrf_private_urls_refused(
    download_wrap: SimpleNamespace,
    url: str,
) -> None:
    """Private, loopback, and file URLs are refused; nothing lands in downloads/."""
    transport = httpx.MockTransport(lambda _req: httpx.Response(200, content=b"x"))
    async with httpx.AsyncClient(transport=transport) as client:
        tool = download_wrap.wrap(client)
        msg = await call_fail(tool, {"url": url})
    assert (
        "blocked: private address" in msg
        or "scheme" in msg.lower()
        or "file" in msg.lower()
    )
    downloads = download_wrap.tools.root / "downloads"
    if downloads.is_dir():
        assert list(downloads.iterdir()) == [] or all(
            p.stat().st_size == 0 for p in downloads.iterdir()
        )


async def test_redirect_to_loopback_refused(download_wrap: SimpleNamespace) -> None:
    """Public URL that redirects to 127.0.0.1 is refused at the redirect hop."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public.example":
            return httpx.Response(
                302,
                headers={"Location": "http://127.0.0.1/secret"},
            )
        return httpx.Response(200, content=b"nope")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download_wrap.wrap(client)
        msg = await call_fail(tool, {"url": "http://public.example/report.pdf"})
    assert "blocked: private address" in msg


async def test_hundred_mb_stream_aborts(download_wrap: SimpleNamespace) -> None:
    """Stream past 100 MB aborts; dest and temps are gone."""

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
        payload = b"x" * (100 * 1024 * 1024 + 64)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/octet-stream"},
            content=payload,
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        tool = download_wrap.wrap(client)
        msg = await call_fail(tool, {"url": "https://example.com/big.bin"})
    assert msg == "too large (limit 100 MB)"
    assert not (download_wrap.tools.root / "downloads" / "big.bin").exists()
    assert not list(download_wrap.tools.root.rglob("*.aldwin-tmp"))


async def test_no_overwrite_existing_destination(
    download_wrap: SimpleNamespace,
) -> None:
    """An existing destination is never overwritten."""
    dest_dir = download_wrap.tools.root / "downloads"
    dest_dir.mkdir(exist_ok=True)
    target = dest_dir / "report.pdf"
    target.write_bytes(b"KEEP")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=b"NEW"))
    ) as client:
        tool = download_wrap.wrap(client)
        msg = await call_fail(
            tool,
            {"url": "http://example.com/report.pdf", "dest": "downloads/report.pdf"},
        )
    assert "overwrite" in msg.lower() or "exists" in msg.lower()
    assert target.read_bytes() == b"KEEP"
