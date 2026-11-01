"""Shell helpers over `ConnectorAdmin` that the CLI calls without a running host."""

from __future__ import annotations

from pathlib import Path

from aldwin.client import connectors_import_command
from aldwin.core.connectors import stdio
from aldwin.core.connectors.config import load_connectors

IMPORTS = Path(__file__).resolve().parent / "fixtures" / "import"


def test_import_never_spawns(aldwin_home: Path) -> None:
    result = connectors_import_command(IMPORTS / "cursor.json", home=aldwin_home)
    assert result["imported"] == 2 and result["values"] == 1
    assert not stdio._LIVE_PGIDS
    github = load_connectors(aldwin_home).entries["github"]
    assert not github.enabled and github.approved_digest is None


async def test_second_sigterm_does_not_cancel_cleanup() -> None:
    from aldwin.host.in_process import _CancelOnSigterm

    guard = _CancelOnSigterm()
    cancels: list[str] = []

    class _Task:
        def cancel(self) -> None:
            cancels.append("cancel")

    guard._task = _Task()  # type: ignore[assignment]
    try:
        guard._on_signal()
        guard._on_signal()
    finally:
        guard.uninstall()
    assert guard.fired and cancels == ["cancel"]
