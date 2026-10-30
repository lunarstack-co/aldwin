"""`download_file`: save a public URL into the workspace after Ask approval."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final
from urllib.parse import unquote, urljoin, urlsplit

import httpx
from agentling import Tool
from agentling.tools import ToolCallError

from aldwin.core.policy import (
    DENIAL_TEXT,
    ApprovalPreview,
    PolicySession,
    is_blocked_path,
)
from aldwin.core.tools import USER_AGENT, ToolOutput
from aldwin.core.tools.files import format_size
from aldwin.core.tools.web_fetch import (
    MAX_REDIRECTS,
    FetchError,
    HostResolver,
    UnsafeUrlError,
    validate_public_url,
)
from aldwin.core.workspace import ResolvedPath, Workspace, WorkspaceError

DOWNLOAD_TIMEOUT_SECONDS: Final[float] = 120.0
MAX_DOWNLOAD_BYTES: Final[int] = 100 * 1024 * 1024
DOWNLOAD_TOOL_NAMES: Final[frozenset[str]] = frozenset({"download_file"})
PROGRAM_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".dmg",
        ".pkg",
        ".app",
        ".exe",
        ".msi",
        ".sh",
        ".command",
        ".bat",
        ".ps1",
        ".jar",
        ".deb",
        ".rpm",
        ".appimage",
    }
)
_REDIRECT_STATUSES: Final[frozenset[int]] = frozenset({301, 302, 303, 307, 308})
_OBJECT_SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "additionalProperties": False,
}
_DISPOSITION_FILENAME_RE: Final[re.Pattern[str]] = re.compile(
    r"""filename\*?=(?:UTF-8''|\"?)([^\";]+)\"?""",
    re.IGNORECASE,
)
ProgressFn = Callable[[int, int | None], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class _DownloadPlan:
    """Resolved probe result shared by prepare and forward."""

    final_url: str
    display: str
    target: Path
    content_type: str
    size: int | None
    filename: str
    first_host: str
    summary: str
    risky: tuple[str, ...]
    detail: str


def running_summary(
    name: str,
    arguments: dict[str, Any] | None,
    workspace: Workspace,
) -> str:
    """Pending row for `download_file`, or `\"\"` for other tools."""
    del workspace
    if name not in DOWNLOAD_TOOL_NAMES:
        return ""
    args = arguments or {}
    dest = args.get("dest")
    if isinstance(dest, str) and dest.strip():
        return f"Download {Path(dest).name}"
    url = args.get("url", "")
    if isinstance(url, str) and url.strip():
        path = urlsplit(url).path
        name_guess = Path(unquote(path)).name if path else ""
        if name_guess and name_guess not in {".", "/"}:
            return f"Download {name_guess}"
    return "Download file"


def row_summary(running: str, summary: str, *, ok: bool) -> str:
    """Rewrite a denial row for download tools."""
    if ok or not running:
        return summary
    lower = summary.lower()
    if summary == "declined" or "denied by the user" in lower:
        return f"{running} · declined"
    if summary == "not run: no approver" or "no one was available to approve" in lower:
        return f"{running} · not run: no one to approve"
    if (summary.startswith("blocked:") and "secret" in lower) or (
        "blocked because it may hold secrets" in lower
    ):
        return f"{running} · blocked: may contain secrets"
    if "safety check failed" in lower:
        return f"{running} · blocked: safety check failed"
    return summary


def sanitize_filename(raw: str) -> str:
    """Strip path separators, NUL, and a leading dot from a download name."""
    name = raw.replace("\x00", "")
    name = name.replace("\\", "/").split("/")[-1].strip()
    name = name.lstrip(".")
    name = re.sub(r"[^\w.\- ()[\]]+", "_", name, flags=re.UNICODE).strip(" ._")
    if not name or name in {".", ".."}:
        return ""
    return name[:200]


def _filename_from_disposition(header: str | None) -> str | None:
    if not header:
        return None
    match = _DISPOSITION_FILENAME_RE.search(header)
    if not match:
        return None
    candidate = unquote(match.group(1).strip().strip("\"'"))
    cleaned = sanitize_filename(candidate)
    return cleaned or None


def _filename_from_url(url: str) -> str | None:
    path = urlsplit(url).path
    if not path or path.endswith("/"):
        return None
    cleaned = sanitize_filename(unquote(Path(path).name))
    return cleaned or None


def _default_download_name() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"download-{stamp}"


def _is_program_file(name: str) -> bool:
    return Path(name).suffix.lower() in PROGRAM_EXTENSIONS


def _host_of(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return host.lower().rstrip(".")


def _media_type(content_type: str | None) -> str:
    if not content_type:
        return "application/octet-stream"
    return content_type.split(";", 1)[0].strip().lower() or "application/octet-stream"


def _blocked_private_message(exc: UnsafeUrlError) -> str:
    text = str(exc)
    lower = text.lower()
    if "private" in lower or "local" in lower or "loopback" in lower:
        return "blocked: private address"
    return text if text.startswith("Refused:") else f"blocked: {text}"


class DownloadFileTool(Tool):
    """Download a public http(s) URL into the workspace after Ask approval."""

    name = "download_file"
    description = (
        "Download a public http or https URL into the workspace (default "
        "downloads/<filename>). Asks for approval first; never overwrites."
    )
    parallel_safe = False
    timeout = None
    max_output_chars = 1_000
    parameters: dict[str, Any] = {
        **_OBJECT_SCHEMA,
        "properties": {
            "url": {
                "type": "string",
                "description": "Public http or https URL to download.",
            },
            "dest": {
                "type": "string",
                "description": (
                    "Optional destination path relative to the workspace. "
                    "Defaults to downloads/<filename>."
                ),
            },
        },
        "required": ["url"],
    }

    def __init__(
        self,
        workspace: Workspace,
        policy_session: PolicySession,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: HostResolver | None = None,
        on_progress: ProgressFn | None = None,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
        timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self._workspace = workspace
        self._session = policy_session
        self._client = client
        self._resolver = resolver
        self._on_progress = on_progress
        self._max_bytes = max_bytes
        self._timeout = timeout
        self._max_redirects = max_redirects

    async def _follow_headers(
        self,
        client: httpx.AsyncClient,
        initial_url: str,
        *,
        method: str,
    ) -> tuple[str, httpx.Headers, int]:
        """Follow redirects with SSRF checks; return final URL, headers, status."""
        current_url = initial_url
        headers = {"User-Agent": USER_AGENT}

        for hop in range(self._max_redirects + 1):
            current_url = await validate_public_url(
                current_url,
                resolver=self._resolver,
            )
            request_method = method if hop == 0 or method == "HEAD" else "GET"
            async with client.stream(
                request_method,
                current_url,
                headers=headers,
                follow_redirects=False,
                timeout=self._timeout,
            ) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location", "").strip()
                    if not location:
                        raise FetchError(
                            f"Redirect (HTTP {response.status_code}) missing Location"
                        )
                    if hop >= self._max_redirects:
                        raise FetchError(
                            "Refused: exceeded maximum redirects "
                            f"({self._max_redirects})."
                        )
                    current_url = urljoin(current_url, location)
                    continue
                if response.status_code >= 400:
                    raise FetchError(
                        f"HTTP {response.status_code} when fetching {current_url}"
                    )
                return (
                    current_url,
                    httpx.Headers(response.headers),
                    response.status_code,
                )

        raise FetchError(
            f"Refused: exceeded maximum redirects ({self._max_redirects})."
        )

    async def _probe(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[str, httpx.Headers]:
        """HEAD with GET fallback; return `(final_url, headers)`."""
        try:
            final_url, headers, _status = await self._follow_headers(
                client, url, method="HEAD"
            )
            return final_url, headers
        except FetchError:
            final_url, headers, _status = await self._follow_headers(
                client, url, method="GET"
            )
            return final_url, headers

    def _resolve_dest(
        self,
        dest: str | None,
        filename: str,
    ) -> tuple[ResolvedPath, str]:
        """Resolve destination; return `(ResolvedPath, display relpath)`."""
        if dest is None or not str(dest).strip():
            rel = f"downloads/{filename}"
        else:
            rel = str(dest).strip()
        try:
            resolved = self._workspace.resolve_safe(
                rel,
                granted_folders=self._session.granted_folders,
            )
        except WorkspaceError as exc:
            raise ToolCallError(str(exc)) from exc
        return resolved, resolved.display

    async def _plan(self, url: str, dest: str | None = None) -> _DownloadPlan:
        """Probe URL, pick dest, and build summary fields (no write)."""
        first_host = _host_of(url)

        try:
            if self._client is not None:
                final_url, headers = await self._probe(self._client, url)
            else:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=False,
                ) as client:
                    final_url, headers = await self._probe(client, url)
        except UnsafeUrlError as exc:
            raise ToolCallError(_blocked_private_message(exc)) from exc
        except FetchError as exc:
            raise ToolCallError(f"download failed: {exc}") from exc
        except httpx.HTTPError as exc:
            reason = " ".join(str(exc).split()) or type(exc).__name__
            raise ToolCallError(f"download failed: {reason}") from exc

        length_raw = headers.get("content-length")
        size: int | None = None
        if length_raw and length_raw.isdigit():
            size = int(length_raw)
            if size > self._max_bytes:
                raise ToolCallError("too large (limit 100 MB)")

        content_type = _media_type(headers.get("content-type"))
        filename = (
            _filename_from_disposition(headers.get("content-disposition"))
            or _filename_from_url(final_url)
            or _filename_from_url(url)
            or _default_download_name()
        )

        resolved, display = self._resolve_dest(dest, filename)
        target = resolved.path

        if is_blocked_path(
            target,
            raw_path=display,
            tool_name=self.name,
            aldwin_home=self._session.home,
        ):
            raise ToolCallError(
                f"Not run: {display} is blocked because it may hold secrets. "
                "This can't be approved."
            )

        if target.exists():
            raise ToolCallError(
                f"{display} already exists; pass dest to save under another name"
            )

        final_host = _host_of(final_url)
        size_label = format_size(size) if size is not None else "unknown size"
        summary = f"Download {filename} ({size_label}) from {final_host} to {display}?"
        if first_host and final_host and first_host != final_host:
            summary = (
                f"Download {filename} ({size_label}) from {final_host} "
                f"(redirected from {first_host}) to {display}?"
            )

        risky: tuple[str, ...] = ()
        if _is_program_file(filename):
            # One trailing ? for the ask panel; risky clause has none of its own.
            summary = f"{summary.rstrip('?')} · Aldwin won't run it?"
            risky = ("program file",)

        detail = f"url={url}\nfinal_url={final_url}\ndest={display}"
        return _DownloadPlan(
            final_url=final_url,
            display=display,
            target=target,
            content_type=content_type,
            size=size,
            filename=filename,
            first_host=first_host,
            summary=summary,
            risky=risky,
            detail=detail,
        )

    async def prepare_approval(
        self,
        url: str,
        dest: str | None = None,
    ) -> ApprovalPreview:
        """Probe the URL, pick dest, and build the Ask summary (no write)."""
        plan = await self._plan(url, dest)
        mtime_ns = plan.target.stat().st_mtime_ns if plan.target.exists() else None
        return ApprovalPreview(
            summary=plan.summary,
            detail=plan.detail,
            mtime_ns=mtime_ns,
            risky=plan.risky,
        )

    async def _stream_to_path(
        self,
        client: httpx.AsyncClient,
        url: str,
        dest: Path,
        *,
        expected_final: str,
        expected_size: int | None,
    ) -> tuple[str, int, str]:
        """Stream GET to `dest`; return `(final_url, bytes_written, content_type)`."""
        current_url = url
        headers = {"User-Agent": USER_AGENT}
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".aldwin-",
            suffix=".aldwin-tmp",
            dir=str(dest.parent),
        )
        tmp_path = Path(tmp_name)
        os.close(fd)
        written = 0
        content_type = "application/octet-stream"
        replaced = False

        try:
            for hop in range(self._max_redirects + 1):
                current_url = await validate_public_url(
                    current_url,
                    resolver=self._resolver,
                )
                async with client.stream(
                    "GET",
                    current_url,
                    headers=headers,
                    follow_redirects=False,
                    timeout=self._timeout,
                ) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        location = response.headers.get("location", "").strip()
                        if not location:
                            raise FetchError(
                                f"Redirect (HTTP {response.status_code}) "
                                "missing Location"
                            )
                        if hop >= self._max_redirects:
                            raise FetchError(
                                "Refused: exceeded maximum redirects "
                                f"({self._max_redirects})."
                            )
                        current_url = urljoin(current_url, location)
                        continue

                    if response.status_code >= 400:
                        raise FetchError(
                            f"HTTP {response.status_code} when fetching {current_url}"
                        )

                    final_host = _host_of(current_url)
                    expected_host = _host_of(expected_final)
                    if final_host != expected_host:
                        raise ToolCallError(DENIAL_TEXT["policy_error"])
                    if urlsplit(current_url).path != urlsplit(expected_final).path:
                        raise ToolCallError(DENIAL_TEXT["policy_error"])

                    content_type = _media_type(response.headers.get("content-type"))
                    length_raw = response.headers.get("content-length")
                    if length_raw and length_raw.isdigit():
                        declared = int(length_raw)
                        if declared > self._max_bytes:
                            raise ToolCallError("too large (limit 100 MB)")
                        if expected_size is None:
                            expected_size = declared

                    with tmp_path.open("wb") as handle:
                        async for chunk in response.aiter_bytes():
                            if not chunk:
                                continue
                            written += len(chunk)
                            if written > self._max_bytes:
                                raise ToolCallError("too large (limit 100 MB)")
                            handle.write(chunk)
                            if self._on_progress is not None:
                                await self._on_progress(written, expected_size)
                            await asyncio.sleep(0)

                    os.replace(tmp_path, dest)
                    replaced = True
                    return current_url, written, content_type

            raise FetchError(
                f"Refused: exceeded maximum redirects ({self._max_redirects})."
            )
        finally:
            if not replaced and tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    async def forward(  # type: ignore[override]
        self,
        url: str,
        dest: str | None = None,
    ) -> ToolOutput:
        # Re-plan (same helpers as prepare). Policy recheck already bound the
        # digest to url/final_url/dest; this picks dest again without storing
        # per-call fields on the tool instance.
        plan = await self._plan(url, dest)
        display = plan.display
        target = plan.target
        expected_final = plan.final_url
        expected_size = plan.size
        content_type = plan.content_type

        try:
            async with asyncio.timeout(self._timeout):
                if self._client is not None:
                    (
                        _final,
                        written,
                        content_type,
                    ) = await self._stream_to_path(
                        self._client,
                        url,
                        target,
                        expected_final=expected_final,
                        expected_size=expected_size,
                    )
                else:
                    async with httpx.AsyncClient(
                        timeout=self._timeout,
                        follow_redirects=False,
                    ) as client:
                        (
                            _final,
                            written,
                            content_type,
                        ) = await self._stream_to_path(
                            client,
                            url,
                            target,
                            expected_final=expected_final,
                            expected_size=expected_size,
                        )
        except TimeoutError as exc:
            if target.exists():
                target.unlink(missing_ok=True)
            raise ToolCallError(f"timed out after {int(self._timeout)}s") from exc
        except UnsafeUrlError as exc:
            raise ToolCallError(_blocked_private_message(exc)) from exc
        except FetchError as exc:
            raise ToolCallError(f"download failed: {exc}") from exc
        except ToolCallError:
            raise
        except httpx.HTTPError as exc:
            reason = " ".join(str(exc).split()) or type(exc).__name__
            raise ToolCallError(f"download failed: {reason}") from exc
        except asyncio.CancelledError:
            for leftover in target.parent.glob(".aldwin-*.aldwin-tmp"):
                leftover.unlink(missing_ok=True)
            if target.exists():
                target.unlink(missing_ok=True)
            raise

        size_label = format_size(written)
        body = f"saved {display} ({size_label}, {content_type})"
        filename = Path(display).name
        summary = f"Download {filename} · {size_label}"
        return ToolOutput(body, summary=summary, ok=True)


def build_download_tool(
    workspace: Workspace,
    policy_session: PolicySession,
    *,
    client: httpx.AsyncClient | None = None,
    resolver: HostResolver | None = None,
    on_progress: ProgressFn | None = None,
) -> DownloadFileTool:
    """Construct the `download_file` tool."""
    return DownloadFileTool(
        workspace,
        policy_session,
        client=client,
        resolver=resolver,
        on_progress=on_progress,
    )


__all__ = [
    "DOWNLOAD_TOOL_NAMES",
    "DownloadFileTool",
    "MAX_DOWNLOAD_BYTES",
    "PROGRAM_EXTENSIONS",
    "build_download_tool",
    "row_summary",
    "running_summary",
    "sanitize_filename",
]
