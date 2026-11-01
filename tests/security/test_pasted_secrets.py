"""A token pasted into chat reaches neither the model nor any file under the home."""

from __future__ import annotations

from pathlib import Path

from canary import CANARY, scan_for_canary
from conftest import collect
from fakes.model import FakeModel, FakeTurn

from aldwin.cli.chat_screen import _create_history
from aldwin.client import AldwinClient


async def test_canary_pasted_in_chat_never_leaves_the_terminal(
    host_client: AldwinClient,
    fake_model: FakeModel,
    aldwin_home: Path,
    canary_env: str,
) -> None:
    line = f"use github_pat_{CANARY}_0123456789abcdef please"
    fake_model.add_turns(FakeTurn.text(["Noted."]))

    _create_history(aldwin_home).append_string(line)
    await collect(host_client.turn(line))

    sent = [message.content or "" for call in fake_model.calls for message in call]
    assert scan_for_canary([aldwin_home], sent) == []
