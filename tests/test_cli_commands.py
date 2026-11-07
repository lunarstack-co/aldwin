"""Unit tests for `SlashCompleter` and slash command handlers."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fakes.host import FakeClient
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from aldwin.cli.commands import SLASH_COMMANDS, SlashCompleter, handle_slash_command
from aldwin.cli.render import ChatRenderer
from aldwin.cli.theme import create_console
from aldwin.protocol import HistoryEvent, HistoryItem


async def _noop_prompt(_label: str) -> str:
    return ""


async def test_slash_model_numbered_list_and_switch() -> None:
    """/model prints numbered list, switches via number or direct name."""
    client = FakeClient(
        model="gpt-4o",
        available_models=["gpt-4o", "gpt-4.1", "o3-mini"],
    )
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    answers = iter(["1", "2"])

    async def pick_second(_label: str) -> str:
        return next(answers)

    should_exit = await handle_slash_command(
        "/model",
        client=client,
        renderer=renderer,
        prompt_line=pick_second,
    )
    assert should_exit is False
    assert client.model == "openai/gpt-4.1"
    assert client.model_switches == ["openai/gpt-4.1"]

    text = console.export_text()
    assert "Available providers" in text
    assert "1. OpenAI (current)" in text
    assert "OpenAI models" in text
    assert "1. gpt-4o (current)" in text
    assert "2. gpt-4.1" in text
    assert "3. o3-mini" in text
    assert "✓ Switched to gpt-4.1 (OpenAI)." in text

    # Direct "/model o3-mini" shortcut
    await handle_slash_command(
        "/model o3-mini",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert client.model == "openai/o3-mini"
    assert client.model_switches == ["openai/gpt-4.1", "openai/o3-mini"]

    # Unknown model shows error
    await handle_slash_command(
        "/model unknown-xyz",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert "Pick another with /model." in console.export_text()

    # Cancel on Enter and invalid number selection
    await handle_slash_command(
        "/model",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert "Model unchanged" in console.export_text()

    async def pick_out_of_range(_label: str) -> str:
        return "99"

    answers_out = iter(["99", ""])

    async def _ans_out(_label: str) -> str:
        return next(answers_out)

    await handle_slash_command(
        "/model",
        client=client,
        renderer=renderer,
        prompt_line=_ans_out,
    )
    assert "Invalid selection '99'" in console.export_text()

    # Dated snapshots are hidden by default and shown with `/model all`
    client.available_models = [
        "gpt-5-2025-08-07",
        "gpt-5",
        "gpt-4o",
        "gpt-4o-2024-11-20",
    ]
    buf_snap = io.StringIO()
    renderer_snap = ChatRenderer(
        create_console(plain=True, width=80, record=True, file=buf_snap),
        plain=True,
    )
    # Pick OpenAI (1) then go back (Enter) then cancel (Enter)
    snap_answers = iter(["1", "", ""])

    async def _ans_snap(_label: str) -> str:
        return next(snap_answers)

    await handle_slash_command(
        "/model",
        client=client,
        renderer=renderer_snap,
        prompt_line=_ans_snap,
    )
    snap_text = renderer_snap.console.export_text()
    assert "gpt-5-2025-08-07" not in snap_text
    assert "use /model all for all 4" in snap_text

    buf_all = io.StringIO()
    renderer_all = ChatRenderer(
        create_console(plain=True, width=80, record=True, file=buf_all),
        plain=True,
    )
    all_answers = iter(["1", "", ""])

    async def _ans_all(_label: str) -> str:
        return next(all_answers)

    await handle_slash_command(
        "/model all",
        client=client,
        renderer=renderer_all,
        prompt_line=_ans_all,
    )
    all_text = renderer_all.console.export_text()
    assert "gpt-5-2025-08-07" in all_text
    assert "gpt-4o-2024-11-20" in all_text


async def test_slash_unknown_command() -> None:
    """/unknown prints 'Unknown command. Type /help.' without starting a turn."""
    client = FakeClient()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    should_exit = await handle_slash_command(
        "/unknown",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert should_exit is False
    assert client.turns == []
    assert "Unknown command. Type /help." in console.export_text()


async def test_slash_todo_prints_list_or_empty() -> None:
    """`/todo` reprints the checklist or the empty-session message."""
    from aldwin.protocol import TodoItem, TodoUpdatedEvent

    client = FakeClient()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    await handle_slash_command(
        "/todo",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert "No task list in this session" in console.export_text()
    assert ("todo.get",) in client.requests

    client.todo_event = TodoUpdatedEvent(
        re="req_todo",
        items=[
            TodoItem(text="List downloads", status="done"),
            TodoItem(text="Rename invoices", status="in_progress"),
        ],
        done=1,
        total=2,
    )
    await handle_slash_command(
        "/todo",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    text = console.export_text()
    assert "Tasks 1/2" in text
    assert "List downloads" in text
    assert "Rename invoices" in text
    assert "/todo" in Path("docs/commands.md").read_text(encoding="utf-8")


async def test_slash_completer_and_inspect_commands() -> None:
    """SlashCompleter yields commands with meta; inspect commands render."""
    completer = SlashCompleter()
    all_items = list(completer.get_completions(Document("/"), CompleteEvent()))
    assert [c.text for c in all_items] == [cmd for cmd, _ in SLASH_COMMANDS]
    assert all(c.display_meta for c in all_items)

    m_items = list(completer.get_completions(Document("/m"), CompleteEvent()))
    assert [c.text for c in m_items] == ["/model", "/memory"]

    none_items = list(completer.get_completions(Document("hello"), CompleteEvent()))
    assert none_items == []

    client = FakeClient()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    tokens_reset = False

    def reset_tokens() -> None:
        nonlocal tokens_reset
        tokens_reset = True

    for cmd in (
        "/help",
        "/new",
        "/history",
        "/persona",
        "/memory",
        "/skills",
        "/tools",
    ):
        res = await handle_slash_command(
            cmd,
            client=client,
            renderer=renderer,
            prompt_line=_noop_prompt,
            on_reset_tokens=reset_tokens,
        )
        assert res is False

    exit_res = await handle_slash_command(
        "/exit",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert exit_res is True
    assert client.new_conversations == 1
    assert tokens_reset is True

    text = console.export_text()
    assert "/help" in text and "Alt+Enter" in text
    assert "new conversation" in text
    assert "~/.aldwin/persona.md" in text
    assert "calm and direct assistant" in text
    assert "~/.aldwin/memory/profile.md" in text
    assert "Notes in the last 7 days: 2" in text
    assert "Fola prefers short answers" in text
    assert "using-aldwin · default ·" in text
    assert "broken-skill · error: missing description" in text
    assert "Add or edit skills in ~/.aldwin/skills/" in text
    assert "web_search" in text
    assert "run_command" in text and "always asks y/n" in text
    assert ("skills.list",) in client.requests


async def test_history_command_prints_saved_messages() -> None:
    """`/history` dumps the conversation transcript on demand."""
    client = FakeClient(
        history_event=HistoryEvent(
            re="req_hist",
            messages=[
                HistoryItem(role="user", text="prior question"),
                HistoryItem(role="assistant", text="prior answer"),
            ],
            interrupted=False,
        )
    )
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    should_exit = await handle_slash_command(
        "/history",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert should_exit is False
    assert ("history", 500) in client.requests
    text = console.export_text()
    assert "> prior question" in text
    assert "* prior answer" in text
    assert "gpt-4o · OpenAI · 2 messages" in text


async def test_skills_show_prints_body() -> None:
    """`/skills show <name>` prints the effective body from the host."""
    client = FakeClient()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    await handle_slash_command(
        "/skills show using-aldwin",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    text = console.export_text()
    assert "Identity and commands" in text
    assert ("skills.show", "using-aldwin") in client.requests

    await handle_slash_command(
        "/skills show missing-skill",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert "No skill named" in console.export_text()


async def test_skills_list_flags_override_and_tools_removed() -> None:
    """`/skills` renders override and tools-removed flags from skills.list."""
    from aldwin.client import SkillsReport, SkillSummary

    client = FakeClient()
    client.custom_skills = SkillsReport(
        valid=(
            SkillSummary(
                name="terminal",
                description="Custom terminal habits for this user.",
                source="user override",
            ),
            SkillSummary(
                name="fancy",
                description="Downloaded skill with code loading stripped.",
                source="user",
                flags=("tools removed",),
            ),
            SkillSummary(
                name="broken",
                description="",
                source="user",
                flags=("error: missing description",),
            ),
        ),
        invalid=(),
    )
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    await handle_slash_command(
        "/skills",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    text = console.export_text()
    assert "terminal · user override ·" in text
    assert "fancy · user · tools removed ·" in text
    assert "broken · error: missing description" in text
    assert "Add or edit skills in ~/.aldwin/skills/" in text


async def test_workspace_command_shows_and_switches_root_and_clears_grants(
    tmp_path: Path,
) -> None:
    """`/workspace` prints workspace and `/workspace <path>` switches root."""
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()

    client = FakeClient()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    await handle_slash_command(
        "/workspace",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    await handle_slash_command(
        f"/workspace {notes_dir}",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    await handle_slash_command(
        "/workspace /nonexistent_dir_xyz",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )

    text = console.export_text()
    assert "workspace: ~/code/aldwin" in text
    assert "also allowed this session: (none)" in text
    assert "session grants cleared" in text
    assert "workspace not found: /nonexistent_dir_xyz" in text


async def test_tc44_verbose_help_readme(aldwin_home: Path) -> None:
    """`/help` and the commands doc list `/verbose` and `/workspace`."""
    config = aldwin_home / "config.yaml"
    before = config.read_bytes() if config.is_file() else b""
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)
    await handle_slash_command(
        "/verbose",
        client=FakeClient(),
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    await handle_slash_command(
        "/verbose",
        client=FakeClient(),
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    await handle_slash_command(
        "/help",
        client=FakeClient(),
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    text = console.export_text()
    assert "verbose: on" in text
    assert "verbose: off" in text
    assert "/verbose" in text
    assert "/workspace" in text
    commands_doc = Path("docs/commands.md").read_text(encoding="utf-8")
    slash = commands_doc.split("## Slash commands", 1)[1].split("## Keyboard", 1)[0]
    assert "/verbose" in slash
    assert "/workspace" in slash
    after = config.read_bytes() if config.is_file() else b""
    assert after == before


async def test_tools_command_labels_follow_approval_tier() -> None:
    """`/tools` shows per-tier notes, not "always asks y/n" for every approval tool."""
    from aldwin.client import get_builtin_tools

    client = FakeClient()
    client.custom_tools = get_builtin_tools()
    buf = io.StringIO()
    console = create_console(plain=True, width=200, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    await handle_slash_command(
        "/tools", client=client, renderer=renderer, prompt_line=_noop_prompt
    )

    lines = {
        line.split()[1]: line
        for line in console.export_text().splitlines()
        if line.strip().startswith("•")
    }
    assert "(always asks y/n)" in lines["run_command"]
    assert "(always asks y/n)" in lines["download_file"]
    assert "(a = allow this file for the session)" in lines["edit_file"]
    assert "(a = allow this file for the session)" in lines["write_file"]
    assert "(asks every time)" in lines["forget"]
    assert "(asks every time)" in lines["create_skill"]
    assert "(replace= shows a diff)" in lines["remember"]
    assert "(" not in lines["read_file"]
    assert "always asks" not in lines["edit_file"]


async def test_slash_search_command(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/search` shows status, switches when ready, and completes provider names."""
    from aldwin.core.tools.web_search import searxng as searxng_mod

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("SEARXNG_URL", raising=False)
    monkeypatch.delenv("SEARXNG_BASE_URL", raising=False)
    monkeypatch.setenv("SEARXNG_URL", "http://127.0.0.1:8080")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test-key-12345678")
    monkeypatch.setattr(searxng_mod, "_probe_searxng_json", lambda *_a, **_k: False)
    searxng_mod.clear_searxng_detection_cache()

    completer = SlashCompleter()
    search_items = list(
        completer.get_completions(Document("/search "), CompleteEvent())
    )
    assert [c.text for c in search_items] == [
        "/search auto",
        "/search searxng",
        "/search tavily",
    ]
    bare_search = list(completer.get_completions(Document("/search"), CompleteEvent()))
    assert [c.text for c in bare_search] == [
        "/search auto",
        "/search searxng",
        "/search tavily",
    ]
    assert all(c.text != "/search" for c in bare_search)

    client = FakeClient()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    await handle_slash_command(
        "/search",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
        home=aldwin_home,
    )
    await handle_slash_command(
        "/search searxng",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
        home=aldwin_home,
    )
    await handle_slash_command(
        "/search brave",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
        home=aldwin_home,
    )

    text = console.export_text()
    assert "Search · Tavily (auto)" in text
    assert "Providers:" in text
    assert "tavily" in text
    assert "searxng" in text
    assert "Search · SearXNG" in text
    assert "not available" in text
    assert "/search" in Path("docs/commands.md").read_text(encoding="utf-8")
    from aldwin.core.settings import load_config

    assert load_config(aldwin_home).search.provider == "searxng"
