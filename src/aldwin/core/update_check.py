"""PyPI update check for the everyday-launch nudge."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Final, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger("aldwin.core.update_check")

PYPI_JSON_URL: Final[str] = "https://pypi.org/pypi/aldwin/json"
FETCH_TIMEOUT_S: Final[float] = 2.0


class _HttpResponse(Protocol):
    def read(self) -> bytes | str: ...


_Opener = Callable[..., AbstractContextManager[_HttpResponse]]


def fetch_latest_version(
    *,
    url: str = PYPI_JSON_URL,
    timeout: float = FETCH_TIMEOUT_S,
    opener: _Opener | None = None,
) -> str | None:
    """Return PyPI ``info.version`` for aldwin, or ``None`` on any failure."""
    open_url: _Opener = opener if opener is not None else cast(_Opener, urlopen)
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "aldwin",
        },
    )
    try:
        with open_url(request, timeout=timeout) as response:
            raw = response.read()
            if isinstance(raw, str):
                raw = raw.encode("utf-8")
            payload = json.loads(raw)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, TypeError) as exc:
        logger.debug("update_check.fetch_failed %s", exc.__class__.__name__)
        return None

    if not isinstance(payload, dict):
        return None
    info = payload.get("info")
    if not isinstance(info, dict):
        return None
    version = info.get("version")
    if not isinstance(version, str):
        return None
    cleaned = version.strip()
    return cleaned or None


def is_newer(latest: str, current: str) -> bool:
    """Return True when ``latest`` is strictly greater than ``current`` (PEP 440)."""
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return False
    try:
        latest_version = Version(latest)
        if latest_version.is_prerelease:
            # pip and uv skip prereleases by default, so a nudge would point
            # at an upgrade that does not happen.
            return False
        return latest_version > Version(current)
    except InvalidVersion:
        return False


def format_nudge(current: str, latest: str) -> str:
    """One-line update nudge with both versions and the upgrade command."""
    return f"Update available: {current} → {latest} · uv tool upgrade aldwin"


def update_nudge_line(
    *,
    current_version: str,
    check_enabled: bool,
    plain: bool,
    fetch: Callable[[], str | None] | None = None,
) -> str | None:
    """Return the nudge line when an update is available; otherwise ``None``."""
    if plain or not check_enabled:
        return None
    fetch_fn = fetch if fetch is not None else fetch_latest_version
    latest = fetch_fn()
    if latest is None:
        return None
    if not is_newer(latest, current_version):
        return None
    return format_nudge(current_version, latest)


__all__ = [
    "FETCH_TIMEOUT_S",
    "PYPI_JSON_URL",
    "fetch_latest_version",
    "format_nudge",
    "is_newer",
    "update_nudge_line",
]
