"""Agentling tool for updating `persona.md` after user `y/n` approval."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from agentling import Tool

from aldwin.core.persona import DEFAULT_PERSONA_CAP, write_persona
from aldwin.core.tools import ToolOutput

AskFn = Callable[[str, str], Awaitable[bool]]


class SetPersonaTool(Tool):
    """Replace `persona.md` only after explicit `y/n` approval via `ask`."""

    name = "set_persona"
    parallel_safe = False
    timeout: float | None = float("inf")

    def __init__(
        self,
        ask: AskFn,
        home: Path | None = None,
        *,
        cap: int = DEFAULT_PERSONA_CAP,
        wrapped_by_policy: bool = False,
    ) -> None:
        self._ask = ask
        self._home = home
        self._cap = cap
        self._wrapped_by_policy = wrapped_by_policy
        # Built per instance so the stated limit follows `limits.persona_chars`.
        self.description = (
            f"Propose a new persona for Aldwin (1-{cap:,} characters). Always asks "
            "the user for y/n approval before updating persona.md."
        )
        self.parameters: dict[str, Any] = {
            "type": "object",
            "properties": {
                "new_text": {
                    "type": "string",
                    "description": (
                        f"Full replacement persona text (1-{cap:,} characters) shown "
                        "to the user for y/n approval."
                    ),
                },
            },
            "required": ["new_text"],
            "additionalProperties": False,
        }

    def _validate_new_text(self, new_text: str) -> tuple[str | None, str | None]:
        """Return `(cleaned_text, error_message)` for `new_text`."""
        if not isinstance(new_text, str) or "\x00" in new_text:
            return None, "Invalid persona: text must be a valid string."
        cleaned = new_text.strip()
        if not cleaned or len(cleaned) > self._cap:
            return (
                None,
                f"Invalid persona: must be between 1 and {self._cap:,} characters "
                f"(got {len(cleaned)}).",
            )
        return cleaned, None

    def prepare_approval(self, new_text: str) -> Any:
        """Build the `ApprovalPreview` for `PolicyWrappedTool`."""
        from aldwin.core.policy import ApprovalPreview

        cleaned, _err = self._validate_new_text(new_text)
        return ApprovalPreview(
            summary="Update persona?",
            detail=cleaned if cleaned is not None else new_text,
            mtime_ns=None,
        )

    async def forward(self, new_text: str) -> str:  # type: ignore[override]
        cleaned, err_msg = self._validate_new_text(new_text)
        if err_msg is not None or cleaned is None:
            msg = err_msg or "Invalid persona."
            return ToolOutput(msg, summary=msg, ok=False)

        if not self._wrapped_by_policy:
            approved = await self._ask("Update persona?", cleaned)
            if not approved:
                return ToolOutput(
                    "user declined",
                    summary="user declined",
                    ok=False,
                    duration_ms=0,
                )

        exec_started = time.monotonic()
        await asyncio.to_thread(
            write_persona,
            self._home,
            cleaned,
            cap=self._cap,
        )
        elapsed_ms = max(0, int((time.monotonic() - exec_started) * 1000))
        return ToolOutput(
            "persona updated",
            summary="persona updated",
            ok=True,
            duration_ms=elapsed_ms,
        )


def build_persona_tool(
    ask: AskFn,
    home: Path | None = None,
    *,
    cap: int = DEFAULT_PERSONA_CAP,
    wrapped_by_policy: bool = False,
) -> SetPersonaTool:
    """Construct the `SetPersonaTool` wired with the host's `ask` callback."""
    return SetPersonaTool(
        ask=ask,
        home=home,
        cap=cap,
        wrapped_by_policy=wrapped_by_policy,
    )
