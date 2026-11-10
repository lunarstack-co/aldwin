"""Tools for host tests that exercise the approval path."""

from __future__ import annotations

from agentling import Tool

from aldwin.host import AskFn


class GatedCommandTool(Tool):
    """Runs a command only after the host's approval answers yes."""

    name = "run_command"
    description = "Run a shell command after approval."
    parameters = {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "Shell command"}},
        "required": ["command"],
    }

    def __init__(self, ask: AskFn) -> None:
        super().__init__()
        self._ask = ask
        self.executed_commands: list[str] = []

    async def forward(self, *, command: str) -> str:  # type: ignore[override]
        if not await self._ask("Run this command?", command):
            return "user declined"
        self.executed_commands.append(command)
        return f"ran: {command}"
