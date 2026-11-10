"""Host integration tests for skill catalogue rebuild and create_skill."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from conftest import auto_approve, collect
from fakes.model import FakeModel, FakeTurn, final, tool_call

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.skills import build_catalogue
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import (
    DoneEvent,
    NoticeEvent,
    SkillsEvent,
    ToolResultEvent,
)

SkillsHost = tuple[AldwinClient, FakeModel, AldwinHost, Path]


def _write_user_skill(
    home: Path, name: str, description: str, body: str = "Step 1.\n"
) -> Path:
    folder = home / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "SKILL.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}",
        encoding="utf-8",
    )
    return path


@pytest.fixture
async def skills_host(
    fake_model: FakeModel,
    aldwin_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[SkillsHost]:
    """In-process host with fake model for skill rebuild tests."""
    root = tmp_path / "ws"
    root.mkdir()
    real_build = build_agent

    def _build(config, secrets, ask, **kwargs):
        kwargs["model"] = fake_model
        return real_build(config, secrets, ask, **kwargs)

    monkeypatch.setattr("aldwin.host.runner.build_agent", _build)
    transport = InProcessTransport()
    host = AldwinHost(transport, home=aldwin_home, workspace=root)
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    try:
        yield client, fake_model, host, aldwin_home
    finally:
        await client.close()
        await host.stop_host()


@pytest.mark.asyncio
async def test_create_skill_approve_rebuilds_and_keeps_memory(
    skills_host: SkillsHost,
) -> None:
    client, model, host, home = skills_host
    model.add_turns(
        tool_call(
            "create_skill",
            {
                "name": "weekly-report",
                "description": "Weekly report Load when asked.",
                "instructions": "1. Gather notes.\n2. Write three bullets.\n",
            },
            call_id="c_skill",
        ),
        FakeTurn.text(["Skill saved."]),
    )
    events = await auto_approve(client, client.turn("make a weekly report skill"))

    path = home / "skills" / "weekly-report" / "SKILL.md"
    assert path.is_file()
    results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
    assert any(
        (ev.summary or "").startswith("Skill created · weekly-report") for ev in results
    )
    notices = [ev for ev in events if isinstance(ev, NoticeEvent)]
    assert any(ev.message == "skills reloaded" for ev in notices)
    skills_events = [ev for ev in events if isinstance(ev, SkillsEvent)]
    assert skills_events
    assert any(item.name == "weekly-report" for item in skills_events[-1].items)
    assert isinstance(events[-1], DoneEvent)

    mem_before = host._agent_session.memory.dump_json()
    host._rebuild_agent()
    assert host._agent_session.memory.dump_json() == mem_before


@pytest.mark.asyncio
async def test_create_skill_decline_writes_nothing(
    skills_host: SkillsHost,
) -> None:
    client, model, _host, home = skills_host
    model.add_turns(
        tool_call(
            "create_skill",
            {
                "name": "weekly-report",
                "description": "Weekly report Load when asked.",
                "instructions": "1. Gather notes.\n",
            },
            call_id="c_skill_no",
        ),
        FakeTurn.text(["Okay, skipped."]),
    )
    events = await auto_approve(client, client.turn("make a skill"), answer="no")

    assert not (home / "skills" / "weekly-report").exists()
    assert not any(
        isinstance(ev, NoticeEvent) and ev.message == "skills reloaded" for ev in events
    )
    results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
    assert results
    assert results[0].ok is False
    assert (
        "Denied" in (results[0].summary or "")
        or "declined" in (results[0].summary or "").lower()
    )


@pytest.mark.asyncio
async def test_delete_override_restores_default_on_next_turn(
    skills_host: SkillsHost,
) -> None:
    client, model, _host, home = skills_host
    _write_user_skill(
        home,
        "terminal",
        "USER TERM Load when testing.",
        "User terminal steps.\n",
    )
    listed = await client.skills_list()
    terminal = next(i for i in listed.items if i.name == "terminal")
    assert terminal.source == "user override"

    import shutil

    shutil.rmtree(home / "skills" / "terminal")

    model.add_turns(final("pong"))
    await collect(client.turn("hi"))

    listed_again = await client.skills_list()
    terminal_again = next(i for i in listed_again.items if i.name == "terminal")
    assert terminal_again.source == "default"


@pytest.mark.asyncio
async def test_hand_edit_triggers_rebuild_before_turn(
    skills_host: SkillsHost,
) -> None:
    client, model, _host, home = skills_host
    path = _write_user_skill(
        home,
        "mine",
        "Mine Load when testing.",
        "Original body.\n",
    )
    # Prime fingerprint after connect catalogue.
    await client.skills_list()

    path.write_text(
        "---\nname: mine\ndescription: Mine Load when testing.\n---\n\nEdited body.\n",
        encoding="utf-8",
    )

    model.add_turns(final("pong"))
    await collect(client.turn("next"))

    body = await client.skills_show("mine")
    assert "Edited body" in body.body


@pytest.mark.asyncio
async def test_skills_list_ten_defaults(skills_host: SkillsHost) -> None:
    client, _model, _host, home = skills_host
    event = await client.skills_list()
    defaults = [
        i
        for i in event.items
        if i.source == "default" and not any(f.startswith("error:") for f in i.flags)
    ]
    assert len(defaults) == 10
    cat = build_catalogue(home, version="0.0.2", model="gpt-test")
    assert {e.name for e in cat if e.skill and e.source == "default"} == {
        i.name for i in defaults
    }
