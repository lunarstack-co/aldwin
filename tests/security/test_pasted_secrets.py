"""A token pasted into chat reaches the model but no file under the home."""

from __future__ import annotations

from pathlib import Path

from canary import CANARY, scan_for_canary
from conftest import collect
from fakes.model import FakeModel, FakeTurn

from aldwin.cli.chat_screen import _create_history
from aldwin.client import AldwinClient


async def test_canary_pasted_in_chat_never_lands_on_disk(
    host_client: AldwinClient,
    fake_model: FakeModel,
    aldwin_home: Path,
    canary_env: str,
) -> None:
    line = f"use github_pat_{CANARY}_0123456789abcdef please"
    fake_model.add_turns(
        FakeTurn.text([f"Noted: github_pat_{CANARY}_0123456789abcdef"])
    )

    _create_history(aldwin_home).append_string(line)
    await collect(host_client.turn(line))

    assert any(CANARY in (message.content or "") for message in fake_model.calls[0])
    assert scan_for_canary([aldwin_home], []) == []
