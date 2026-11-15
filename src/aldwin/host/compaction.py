"""Making room in a long conversation before a turn, or after a refused request.

The saved transcript is never touched; only the model's view changes.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from agentling import (
    ChatMessage,
    Delta,
    Model,
    ModelError,
    ToolDefinition,
    Usage,
    agglomerate_deltas,
)
from agentling.memory import Step

from aldwin.core import compaction as c
from aldwin.core.approval_log import redact_secrets

logger = logging.getLogger("aldwin.host.compaction")

# Room for the summary instructions beside the notes and the piece.
_SUMMARY_PROMPT_TOKENS = 600

Summariser = Callable[
    [str | None, str, int], Awaitable[tuple[str, str | None, Usage | None]]
]


@dataclass(frozen=True)
class Sizer:
    """Estimates request size for a candidate compaction state."""

    fixed: int
    conversation: Sequence[ChatMessage]
    steps: Sequence[Step]
    offset: int = 0

    def size(self, state: c.CompactionState) -> int:
        """Tokens a request would use if the model saw the chat through `state`."""
        view = c.build_view(self.steps, state)
        shaped = c.apply_view(self.conversation, view)
        return self.fixed + c.estimate_tokens(shaped) + self.offset


@dataclass(frozen=True)
class Room:
    """The model's window, as far as making room is concerned."""

    window: int
    limit: int
    ceiling: int


@dataclass
class RoomOutcome:
    """What making room decided; the caller saves `state` and shows the notice."""

    state: c.CompactionState
    notice: str | None = None
    stop: str | None = None
    usage: Usage | None = None
    retired: tuple[int, int] = (0, 0)
    shortened: tuple[int, int] = (0, 0)


@dataclass
class _Summary:
    text: str | None
    covered_to: int
    usage: Usage | None
    failed: bool = False


def model_summariser(model: Model, timeout: float) -> Summariser:
    """A summariser that streams one request at a time from `model`, with no tools."""

    async def summarise(
        previous: str | None, piece: str, target: int
    ) -> tuple[str, str | None, Usage | None]:
        messages = c.summary_messages(previous, piece, target)
        deltas: list[Delta] = []
        async with asyncio.timeout(timeout):
            async for delta in model.stream(messages, ()):
                deltas.append(delta)
        message = agglomerate_deltas(deltas)
        return message.content, message.finish_reason, message.usage

    return summarise


def fixed_tokens(system: ChatMessage, tool_defs: Sequence[ToolDefinition]) -> int:
    """Tokens of the part of every request that does not grow with the chat."""
    return c.estimate_tokens([system], tool_defs)


async def make_room(
    *,
    steps: Sequence[Step],
    state: c.CompactionState,
    sizer: Sizer,
    room: Room,
    task_tokens: int | None,
    summarise: Summariser,
    now: Callable[[], datetime],
    on_status: Callable[[str], Awaitable[None]],
    model_name: str,
) -> RoomOutcome:
    """Shorten old tool results, summarise, then leave out turns until it fits.

    `task_tokens` is None when the user makes room by hand, which works below the limit.
    """
    by_hand = task_tokens is None
    if sizer.fixed > room.ceiling:
        return RoomOutcome(state=state, stop="too_small")
    if task_tokens is not None and sizer.fixed + task_tokens > room.ceiling:
        return RoomOutcome(state=state, stop="message_too_long")

    # A model whose fixed part alone is past the line still runs, without summaries.
    tiny = sizer.fixed > room.limit
    trigger = room.ceiling if tiny else room.limit
    if not by_hand and sizer.size(state) <= trigger:
        return RoomOutcome(state=state)

    outcome = RoomOutcome(state=state)
    start = state.cut
    shrank = False
    if not by_hand:
        await on_status(c.MESSAGES["making"])
    if state.shortened_through < len(steps):
        before = sizer.size(state)
        outcome.shortened = (state.shortened_through, len(steps))
        state = replace(state, shortened_through=len(steps))
        after = sizer.size(state)
        shrank = after < before
        small = after - sizer.fixed <= c.room_max(room.window)
        fits = after <= room.ceiling and (after <= trigger if tiny else small)
        if not by_hand and fits:
            outcome.state = state
            outcome.notice = "shortened" if shrank else None
            return outcome

    cut = c.choose_cut(
        steps,
        start=start,
        budget=c.tail_budget(room.window),
        new_task_tokens=task_tokens,
        shortened_through=state.shortened_through,
    )
    if by_hand and cut is None:
        if shrank:
            outcome.state = state
            outcome.notice = "shortened"
            return outcome
        return RoomOutcome(state=state, stop="still_short")

    summarised = False
    if cut is not None and not tiny and c.may_summarise(state, now()):
        await on_status(c.MESSAGES["working"])
        summary = await _summarise_turns(steps, state, cut, room, summarise)
        outcome.usage = summary.usage
        if summary.covered_to > state.cut and summary.text:
            state = _with_summary(state, steps, summary, model_name, now())
            summarised = True
        if summary.failed:
            failures = state.failures + 1
            retry = c.next_retry_after(failures, now()).isoformat()
            state = replace(state, failures=failures, retry_after=retry)

    # After a summary, turns are left out only if the request would not fit at all.
    target = room.ceiling if summarised else trigger
    if sizer.size(state) > target:
        state = _leave_out(steps, state, sizer, target, current_in_steps=False)

    if by_hand and not summarised and not shrank and state.cut == start:
        stop = "still_short" if tiny else "notes_failed"
        return RoomOutcome(state=state, stop=stop, usage=outcome.usage)

    outcome.state = state
    outcome.retired = (start, state.cut)
    if sizer.size(state) > room.ceiling:
        outcome.stop = "did_not_help"
    elif summarised and state.left_out == 0:
        outcome.notice = "summarised"
    elif state.cut > start:
        outcome.notice = "fallback"
    elif shrank:
        outcome.notice = "shortened"
    return outcome


def leave_out_after_refusal(
    *,
    steps: Sequence[Step],
    state: c.CompactionState,
    sizer: Sizer,
    room: Room | None,
) -> c.CompactionState | None:
    """Move the cut forward after a refused request, or None when nothing is left.

    With no known window, half of the older turns still in view are left out.
    """
    target = room.limit if room is not None else None
    moved = _leave_out(steps, state, sizer, target, current_in_steps=True)
    return moved if moved.cut > state.cut else None


def _leave_out(
    steps: Sequence[Step],
    state: c.CompactionState,
    sizer: Sizer,
    target: int | None,
    *,
    current_in_steps: bool,
) -> c.CompactionState:
    def moved_to(cut: int) -> c.CompactionState:
        return replace(
            state,
            cut=cut,
            left_out=state.left_out + c.rendered_between(steps, state.cut, cut),
            files=c.files_from_steps(steps[state.cut : cut], state.files),
        )

    if target is None:
        halfway = _halfway_turn(steps, state.cut)
        return moved_to(halfway) if halfway is not None else state
    cut = c.fallback_cut(
        steps,
        start=state.cut,
        fits=lambda candidate: sizer.size(moved_to(candidate)) <= target,
        current_turn_in_steps=current_in_steps,
    )
    return moved_to(cut) if cut > state.cut else state


def _halfway_turn(steps: Sequence[Step], start: int) -> int | None:
    """The start of the middle turn after `start`, never past the latest turn."""
    later = [index for index in c.turn_starts(steps) if index > start]
    return later[(len(later) - 1) // 2] if later else None


def _with_summary(
    state: c.CompactionState,
    steps: Sequence[Step],
    summary: _Summary,
    model_name: str,
    now: datetime,
) -> c.CompactionState:
    return replace(
        state,
        summary=redact_secrets(summary.text or ""),
        cut=summary.covered_to,
        left_out=0,
        files=c.files_from_steps(steps[state.cut : summary.covered_to], state.files),
        model=model_name,
        at=now.isoformat(),
        failures=0,
        retry_after=None,
    )


def turn_groups(
    steps: Sequence[Step], start: int, end: int, max_chars: int
) -> list[int]:
    """End indexes of groups of whole turns whose transcript fits in max_chars.

    A turn larger than max_chars is a group of its own; it is summarised in pieces.
    """
    bounds = [*(i for i in c.turn_starts(steps) if start < i < end), end]
    ends: list[int] = []
    group_start = start
    previous = start
    for turn_end in bounds:
        too_big = len(c.transcript_text(steps[group_start:turn_end])) > max_chars
        if too_big and previous > group_start:
            ends.append(previous)
            group_start = previous
        previous = turn_end
    ends.append(end)
    return ends


async def _summarise_turns(
    steps: Sequence[Step],
    state: c.CompactionState,
    cut: int,
    room: Room,
    summarise: Summariser,
) -> _Summary:
    """Summarise steps[state.cut:cut] group by group, updating the running notes.

    A failure keeps the notes for the groups already done.
    """
    target = c.summary_target(room.window)
    piece_tokens = room.ceiling - 2 * target - _SUMMARY_PROMPT_TOKENS
    max_chars = max(2_000, int(piece_tokens * c.CHARS_PER_TOKEN))
    running = state.summary
    covered = state.cut
    total: Usage | None = None
    for group_end in turn_groups(steps, state.cut, cut, max_chars):
        notes = running
        text = c.transcript_text(steps[covered:group_end])
        try:
            for piece in c.split_pieces(text, max_chars):
                result, finish, usage = await summarise(notes, piece, target)
                if usage is not None:
                    total = usage if total is None else total + usage
                retired_chars = len(notes or "") + len(piece)
                reason = c.check_summary(result, finish, retired_chars)
                if reason is not None:
                    logger.warning("summary rejected: %s", reason)
                    return _Summary(running, covered, total, failed=True)
                notes = result.strip()
        except (ModelError, TimeoutError) as exc:
            logger.warning("summary request failed: %s", type(exc).__name__)
            return _Summary(running, covered, total, failed=True)
        running = notes
        covered = group_end
    return _Summary(running, covered, total)
