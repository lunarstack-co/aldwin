"""Tests for `aldwin.core.agent` (`build_agent`, `load_skills`, and model switch)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest
from agentling import tool
from fakes.model import FakeModel, FakeTurn

from aldwin.core.agent import (
    build_agent,
    load_base_instructions,
    load_skills,
    new_session,
    rebuild_agent,
    restore_session,
    switch_session_model,
)
from aldwin.core.home import ensure_home
from aldwin.core.settings import AldwinConfig, SecretsConfig


async def _dummy_ask(_summary: str, _detail: str = "") -> bool:
    return True


def test_build_agent_default_config(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """`build_agent` wires tools, starter skill, `max_steps=15`, and context."""
    ensure_home(aldwin_home)

    @tool
    def stub_tool(query: str) -> str:
        """Stub tool for agent factory test.

        Args:
            query: Search query.
        """
        return query

    config = AldwinConfig()
    secrets = SecretsConfig()
    agent = build_agent(
        config,
        secrets,
        _dummy_ask,
        home=aldwin_home,
        model=fake_model,
        tools=[stub_tool],
    )

    assert agent.model is fake_model
    assert agent.max_steps == 15
    assert agent.tool_timeout == 60.0
    assert agent.max_tool_output_chars == 10_000
    assert agent.context_manager is not None
    assert "using-aldwin" in agent.skills
    session = agent.start()
    assert "stub_tool" in session.tools
    assert "final_answer" in session.tools


def test_load_skills_skips_broken_and_reports_invalid(
    aldwin_home: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Valid skills load; broken `SKILL.md` is logged and reported as invalid."""
    ensure_home(aldwin_home)
    skills_dir = aldwin_home / "skills"

    good_dir = skills_dir / "good-skill"
    good_dir.mkdir(parents=True)
    (good_dir / "SKILL.md").write_text(
        "---\nname: good-skill\ndescription: A valid skill.\n---\nUse this well.\n",
        encoding="utf-8",
    )

    bad_dir = skills_dir / "broken-skill"
    bad_dir.mkdir(parents=True)
    (bad_dir / "SKILL.md").write_text(
        "---\nname: broken-skill\n---\nMissing description key.\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="aldwin.core.skills"):
        result = load_skills(skills_dir)

    names = [s.name for s in result.valid]
    assert "good-skill" in names
    assert "using-aldwin" in names
    assert "documents" in names
    assert any(inv.name == "broken-skill" for inv in result.invalid)
    broken = next(inv for inv in result.invalid if inv.name == "broken-skill")
    assert "description" in broken.reason
    assert any("broken-skill" in rec.getMessage() for rec in caplog.records)


def test_load_skills_duplicate_name_first_wins_and_rejects_skill_tools(
    aldwin_home: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Duplicate skill names keep the first; `tools:` and reserved names fail."""
    ensure_home(aldwin_home)
    skills_dir = aldwin_home / "skills"

    alpha_dir = skills_dir / "01_alpha"
    alpha_dir.mkdir(parents=True)
    (alpha_dir / "SKILL.md").write_text(
        "---\nname: writer\ndescription: First writer.\n---\nFirst body.\n",
        encoding="utf-8",
    )

    beta_dir = skills_dir / "02_beta"
    beta_dir.mkdir(parents=True)
    (beta_dir / "SKILL.md").write_text(
        "---\nname: writer\ndescription: Second writer.\n---\nSecond body.\n",
        encoding="utf-8",
    )

    evil_dir = skills_dir / "03_evil"
    evil_dir.mkdir(parents=True)
    (evil_dir / "SKILL.md").write_text(
        "---\nname: evil\ndescription: Bad.\ntools:\n  - os:system\n---\n",
        encoding="utf-8",
    )

    reserved_dir = skills_dir / "04_reserved"
    reserved_dir.mkdir(parents=True)
    (reserved_dir / "SKILL.md").write_text(
        "---\nname: load_skill\ndescription: Collides with built-in.\n---\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="aldwin.core.skills"):
        result = load_skills(skills_dir)

    by_name = {s.name: s for s in result.valid}
    assert "writer" in by_name
    # Folder whose dirname matches frontmatter name wins; neither 01_alpha nor
    # 02_beta is named "writer", so the first sorted candidate is kept.
    assert by_name["writer"].description in {"First writer.", "Second writer."}
    # tools: is stripped; skill remains as text-only.
    assert "evil" in by_name
    assert by_name["evil"].tools == []
    assert "load_skill" not in by_name

    invalid_names = {inv.name: inv.reason for inv in result.invalid}
    assert any("duplicate" in reason for reason in invalid_names.values())
    assert "04_reserved" in invalid_names or any(
        "reserved" in r for r in invalid_names.values()
    )


def test_load_skills_handles_missing_dir_missing_file_and_blank_fields(
    aldwin_home: Path,
) -> None:
    """`load_skills` handles folders without SKILL.md and blank fields."""
    # Missing user skills dir still returns packaged defaults via catalogue.
    missing = load_skills(aldwin_home / "does-not-exist")
    assert "using-aldwin" in {s.name for s in missing.valid}

    ensure_home(aldwin_home)
    skills_dir = aldwin_home / "skills"
    (skills_dir / "empty-folder").mkdir()

    blank_name_dir = skills_dir / "blank-name"
    blank_name_dir.mkdir()
    (blank_name_dir / "SKILL.md").write_text(
        "---\nname: '   '\ndescription: Valid desc.\n---\n",
        encoding="utf-8",
    )

    blank_desc_dir = skills_dir / "blank-desc"
    blank_desc_dir.mkdir()
    (blank_desc_dir / "SKILL.md").write_text(
        "---\nname: ok-name\ndescription: '   '\n---\n",
        encoding="utf-8",
    )

    multiline_name_dir = skills_dir / "multiline-name"
    multiline_name_dir.mkdir()
    (multiline_name_dir / "SKILL.md").write_text(
        "---\nname: |\n  line1\n  line2\ndescription: Valid desc.\n---\n",
        encoding="utf-8",
    )

    multiline_desc_dir = skills_dir / "multiline-desc"
    multiline_desc_dir.mkdir()
    (multiline_desc_dir / "SKILL.md").write_text(
        "---\nname: clean-desc\ndescription: |\n"
        "  First line\n  ## Injected heading\n---\n",
        encoding="utf-8",
    )

    result = load_skills(skills_dir)
    by_name = {s.name: s for s in result.valid}
    assert by_name["clean-desc"].description == "First line ## Injected heading"

    reasons = {inv.name: inv.reason for inv in result.invalid}
    assert "empty-folder" in reasons
    assert "missing SKILL.md" in reasons["empty-folder"]
    assert "blank-name" in reasons
    assert "name" in reasons["blank-name"]
    assert "blank-desc" in reasons
    assert "description" in reasons["blank-desc"]
    assert "multiline-name" in reasons
    assert "single line" in reasons["multiline-name"]


def test_build_and_rebuild_agent_default_openai_model_and_validation(
    aldwin_home: Path,
) -> None:
    """`build_agent` builds an `OpenAIChatModel` when `model=None`."""
    from agentling import RetryPolicy
    from pydantic import SecretStr

    from aldwin.core.providers.openai_chat import OpenAIChatModel

    ensure_home(aldwin_home)
    secrets = SecretsConfig(openai_api_key=SecretStr("sk-test-1234567890"))
    agent = build_agent(
        AldwinConfig(model="gpt-4o"),
        secrets,
        home=aldwin_home,
    )
    assert isinstance(agent.model, OpenAIChatModel)
    assert isinstance(agent.retry, RetryPolicy)

    rebuilt, updated = rebuild_agent(
        AldwinConfig(model="gpt-4o"),
        secrets,
        None,
        "gpt-4.1",
        home=aldwin_home,
    )
    assert isinstance(rebuilt.model, OpenAIChatModel)
    assert isinstance(rebuilt.retry, RetryPolicy)
    assert updated.model == "gpt-4.1"

    with pytest.raises(ValueError, match="model_name"):
        rebuild_agent(AldwinConfig(), secrets, None, "   ", home=aldwin_home)


async def test_skill_catalog_appears_once_in_system_message(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """Skill catalog appears exactly once in the rendered system prompt."""
    ensure_home(aldwin_home)
    fake_model.add_turns(FakeTurn.text(["Ready to help."]))

    agent = build_agent(
        AldwinConfig(),
        SecretsConfig(),
        _dummy_ask,
        home=aldwin_home,
        model=fake_model,
    )
    session = new_session(agent)
    answer = await session.run("What skills do you have?", stream=False, reset=False)

    assert answer == "Ready to help."
    sys_msg = fake_model.received_messages[0]
    assert sys_msg.role == "system"
    assert sys_msg.content.count("Available skills:") == 1
    assert sys_msg.content.count("- using-aldwin:") == 1
    assert "\n\n## Persona\n" in sys_msg.content


def test_base_instructions_sections_and_safety_rules() -> None:
    """Base instructions cover identity, narration, tools, memory, and safety."""
    text = load_base_instructions()
    lower = text.lower()

    assert "aldwin" in lower
    assert "one-line plan" in lower
    assert "2 to 3 tool calls" in lower
    assert "remember" in lower
    assert "never store api keys" in lower
    assert "<untrusted>" in lower
    assert "<memory>" in lower
    assert "never instructions" in lower


async def test_new_and_restore_session_roundtrip(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """`new_session` and `restore_session` preserve full conversation memory."""
    ensure_home(aldwin_home)
    fake_model.add_turns(
        FakeTurn.text(["Hello Fola!"]),
        FakeTurn.text(["You just greeted me."]),
    )
    agent = build_agent(
        AldwinConfig(),
        SecretsConfig(),
        _dummy_ask,
        home=aldwin_home,
        model=fake_model,
    )
    session = new_session(agent)
    await session.run("Hi, I'm Fola", stream=False, reset=False)

    dumped = session.memory.dump_json()
    restored = restore_session(agent, dumped)
    assert restored.memory.to_dict() == session.memory.to_dict()

    await restored.run("What did I say?", stream=False, reset=False)
    user_contents = [
        m.content for m in fake_model.received_messages if m.role == "user"
    ]
    assert user_contents == ["Hi, I'm Fola", "What did I say?"]


async def test_set_model_mid_conversation_preserves_memory(
    aldwin_home: Path,
) -> None:
    """`rebuild_agent` + `switch_session_model` keeps history."""
    ensure_home(aldwin_home)
    model_a = FakeModel(turns=[FakeTurn.text(["Hi Fola from model A"])])
    model_b = FakeModel(turns=[FakeTurn.text(["Your name is Fola from model B"])])

    config = AldwinConfig(model="gpt-4o")
    secrets = SecretsConfig()
    agent_a = build_agent(
        config,
        secrets,
        _dummy_ask,
        home=aldwin_home,
        model=model_a,
    )
    session_a = new_session(agent_a)
    await session_a.run("My name is Fola.", stream=False, reset=False)

    agent_b, updated_config = rebuild_agent(
        config,
        secrets,
        _dummy_ask,
        "gpt-4.1",
        home=aldwin_home,
        model=model_b,
    )
    assert updated_config.model == "gpt-4.1"
    session_b = switch_session_model(session_a, agent_b)

    reply = await session_b.run("What is my name?", stream=False, reset=False)
    assert reply == "Your name is Fola from model B"
    assert len(model_a.calls) == 1
    assert len(model_b.calls) == 1
    assert [
        (m.role, m.content) for m in model_b.received_messages if m.role != "system"
    ] == [
        ("user", "My name is Fola."),
        ("assistant", "Hi Fola from model A"),
        ("user", "What is my name?"),
    ]


def test_build_agent_performance_under_200ms(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """`build_agent` completes in < 200 ms with multiple skills."""
    ensure_home(aldwin_home)
    skills_dir = aldwin_home / "skills"
    for idx in range(3):
        folder = skills_dir / f"skill-{idx}"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: skill-{idx}\ndescription: Skill {idx}\n---\nBody {idx}\n",
            encoding="utf-8",
        )

    t0 = time.perf_counter()
    agent = build_agent(
        AldwinConfig(),
        SecretsConfig(),
        _dummy_ask,
        home=aldwin_home,
        model=fake_model,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # 9 defaults + 3 user skills
    assert len(agent.skills) == 13
    assert elapsed_ms < 200.0
