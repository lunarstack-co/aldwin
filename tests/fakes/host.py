"""Fake client/host double for testing Aldwin CLI screens and slash commands."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path

from aldwin.client import (
    InvalidSkillSummary,
    MemorySummary,
    NoteEntry,
    PersonaSummary,
    SkillsReport,
    SkillSummary,
    ToolInfo,
    WorkspaceSummary,
    resolve_workspace_summary,
)
from aldwin.protocol import (
    ApprovalAnswer,
    DoneEvent,
    ErrorEvent,
    Event,
    HistoryEvent,
    SkillBodyEvent,
    SkillCatalogItem,
    SkillsEvent,
    TodoUpdatedEvent,
)


class FakeHost:
    """Scriptable host/client test double for the CLI (``, `aldwin-testing`)."""

    def __init__(
        self,
        *,
        model: str = "gpt-4o",
        session_id: str = "2026-10-18_3f2a9b1c",
        updated_at: str | None = "2026-10-17T18:02:00+00:00",
        available_models: list[str] | None = None,
        history_event: HistoryEvent | None = None,
    ) -> None:
        self.model = model
        self.session_id = session_id
        self.updated_at = updated_at
        self.available_models = available_models or ["gpt-4o", "gpt-4.1", "o3-mini"]
        self.history_event = history_event or HistoryEvent(
            re="req_hist",
            messages=[],
            interrupted=False,
        )

        self.connected = False
        self.closed = False
        self.turns: list[str] = []
        self.approvals: list[tuple[str, ApprovalAnswer]] = []
        self.approval_details: list[
            tuple[str, ApprovalAnswer, str | None, str | None]
        ] = []
        self.stop_calls = 0
        self.model_switches: list[str] = []
        self.new_conversations = 0
        self.todo_event = TodoUpdatedEvent(re="req_todo", items=[], done=0, total=0)
        self.requests: list[tuple[object, ...]] = []
        self.custom_workspace = WorkspaceSummary(
            root=Path("/tmp/ws"),
            display_root="~/code/aldwin",
            granted_folders=(),
        )

        self._scripted_turns: list[list[Event]] = []
        self.hang_until_stopped = False
        self._stop_gate = asyncio.Event()
        self.turn_started_event = asyncio.Event()
        self.approval_emitted_event = asyncio.Event()

        self.custom_persona = PersonaSummary(
            path=Path("/tmp/.aldwin/persona.md"),
            display_path="~/.aldwin/persona.md",
            text="You are Aldwin, a calm and direct assistant.",
            size_chars=45,
            cap_chars=2000,
            truncated=False,
        )
        self.custom_memory = MemorySummary(
            profile_path=Path("/tmp/.aldwin/memory/profile.md"),
            display_profile_path="~/.aldwin/memory/profile.md",
            profile_size_chars=120,
            profile_cap_chars=4000,
            profile_truncated=False,
            notes_window_days=7,
            notes_in_window=2,
            recent_notes=(
                NoteEntry(date="2026-10-18", text="Fola prefers short answers"),
                NoteEntry(date="2026-10-17", text="Agentling docs move comes first"),
            ),
        )
        self.custom_skills = SkillsReport(
            valid=(
                SkillSummary(
                    name="using-aldwin",
                    description="How to use Aldwin's memory, persona, and tools.",
                    source="default",
                ),
                SkillSummary(
                    name="broken-skill",
                    description="",
                    source="user",
                    flags=("error: missing description",),
                ),
            ),
            invalid=(
                InvalidSkillSummary(
                    name="broken-skill",
                    display_path="~/.aldwin/skills/broken-skill",
                    reason="missing description",
                ),
            ),
        )
        self.custom_skill_bodies: dict[str, str] = {
            "using-aldwin": "# Using Aldwin\n\nIdentity and commands.\n",
        }
        self.custom_tools: list[ToolInfo] = [
            ToolInfo(
                name="web_search",
                description="Search the web with Tavily and return top results.",
                requires_approval=False,
            ),
            ToolInfo(
                name="web_fetch",
                description="Fetch a web page as readable text.",
                requires_approval=False,
            ),
            ToolInfo(
                name="run_command",
                description="Run a shell command after y/n approval.",
                requires_approval=True,
            ),
            ToolInfo(
                name="remember",
                description="Save a short fact to today's memory notes.",
                requires_approval=False,
            ),
            ToolInfo(
                name="recall",
                description="Search recent memory notes by keywords.",
                requires_approval=False,
            ),
            ToolInfo(
                name="forget",
                description="Delete a saved memory note after approval.",
                requires_approval=True,
            ),
        ]

    def queue_turn(self, events: list[Event]) -> None:
        """Append a scripted sequence of protocol events for the next `turn` call."""
        self._scripted_turns.append(list(events))

    async def connect(self) -> DoneEvent:
        self.connected = True
        self.requests.append(("connect",))
        return DoneEvent(re="req_hello", reason="ok")

    async def history(self, limit: int = 20) -> HistoryEvent:
        self.requests.append(("history", limit))
        return self.history_event

    async def turn(
        self,
        text: str,
        *,
        skills: Sequence[str] = (),
    ) -> AsyncGenerator[Event, None]:
        self.turns.append(text)
        self.requests.append(("turn", text, list(skills)))
        self._stop_gate.clear()
        self.turn_started_event.set()
        events = (
            self._scripted_turns.pop(0)
            if self._scripted_turns
            else [DoneEvent(re="req_turn", reason="answered")]
        )
        for ev in events:
            if ev.kind == "approval":
                self.approval_emitted_event.set()
            yield ev
            await asyncio.sleep(0)
        if self.hang_until_stopped:
            await self._stop_gate.wait()
            yield DoneEvent(re="req_turn", reason="stopped")

    async def stop(self) -> DoneEvent | ErrorEvent:
        self.stop_calls += 1
        self.requests.append(("stop",))
        self._stop_gate.set()
        return DoneEvent(re="req_stop", reason="ok")

    async def approve(
        self,
        approval_id: str,
        answer: ApprovalAnswer,
        *,
        digest: str | None = None,
        reason: str | None = None,
    ) -> DoneEvent | ErrorEvent:
        self.approvals.append((approval_id, answer))
        self.approval_details.append((approval_id, answer, digest, reason))
        self.requests.append(("approve", approval_id, answer))
        return DoneEvent(re="req_approve", reason="ok")

    async def set_model(self, name: str) -> DoneEvent | ErrorEvent:
        cleaned = name.strip()
        self.requests.append(("set_model", cleaned))
        if cleaned not in self.available_models:
            return ErrorEvent(
                re="req_model",
                code="unknown_model",
                message=f"Model {cleaned!r} is not available.",
            )
        self.model = cleaned
        self.model_switches.append(cleaned)
        return DoneEvent(re="req_model", reason="ok")

    async def new_conversation(self) -> None:
        self.new_conversations += 1
        self.requests.append(("new_conversation",))
        self.session_id = f"2026-10-18_new{self.new_conversations:02d}"
        self.todo_event = TodoUpdatedEvent(re="req_todo", items=[], done=0, total=0)

    async def todo_get(self) -> TodoUpdatedEvent:
        self.requests.append(("todo.get",))
        return self.todo_event

    async def list_models(self) -> list[str]:
        return list(self.available_models)

    def persona_summary(self) -> PersonaSummary:
        return self.custom_persona

    def memory_summary(self) -> MemorySummary:
        return self.custom_memory

    def skills_report(self) -> SkillsReport:
        return self.custom_skills

    async def skills_list(self) -> SkillsEvent:
        self.requests.append(("skills.list",))
        items = tuple(
            SkillCatalogItem(
                name=s.name,
                description=s.description,
                source=s.source,
                flags=list(getattr(s, "flags", ())),
                last_used=getattr(s, "last_used", None),
            )
            for s in self.custom_skills.valid
        )
        return SkillsEvent(re="req_skills", items=list(items))

    async def skills_show(self, name: str) -> SkillBodyEvent:
        from aldwin.client.client import ProtocolError

        self.requests.append(("skills.show", name))
        if name not in self.custom_skill_bodies:
            raise ProtocolError(f"No skill named {name!r}.")
        return SkillBodyEvent(
            re="req_skill_body",
            name=name,
            body=self.custom_skill_bodies[name],
        )

    def tools_summary(self) -> list[ToolInfo]:
        return list(self.custom_tools)

    def workspace_summary(self) -> WorkspaceSummary:
        return self.custom_workspace

    def set_workspace(self, new_root: Path | str) -> WorkspaceSummary:
        self.custom_workspace = resolve_workspace_summary(new_root)
        return self.custom_workspace

    async def close(self) -> None:
        self.closed = True


FakeClient = FakeHost
