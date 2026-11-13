"""prompt_toolkit `@` skill completer for the Aldwin chat input."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.lexers import Lexer

from aldwin.client import sanitize_display_text
from aldwin.protocol import SkillCatalogItem

SKILL_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:(?<=\s)|^)@([a-z0-9]+(?:-[a-z0-9]+)*)"
)
MAX_PICKER_ROWS: Final[int] = 8
NO_MATCH_TMPL: Final[str] = 'No skill called "{text}" · /skills lists them all'


@dataclass(slots=True)
class SkillCache:
    """Mutable skill catalogue cache for the completer (never blocks on I/O)."""

    items: list[SkillCatalogItem]

    def replace(self, items: Sequence[SkillCatalogItem]) -> None:
        """Replace the cached catalogue (from a `skills` event)."""
        self.items = list(items)

    def known_names(self) -> set[str]:
        """Names that are valid to send (excludes error-flagged skills)."""
        return {
            item.name
            for item in self.items
            if not any(f.startswith("error:") for f in item.flags)
        }


def extract_skill_tokens(text: str, known: set[str]) -> list[str]:
    """Extract `@skill` tokens that match `known`, in order of appearance."""
    found: list[str] = []
    for match in SKILL_TOKEN_RE.finditer(text):
        name = match.group(1)
        if name in known:
            found.append(name)
    return found


def _is_error_item(item: SkillCatalogItem) -> bool:
    return any(flag.startswith("error:") for flag in item.flags)


def _fuzzy_rank(query: str, item: SkillCatalogItem) -> tuple[int, int, str] | None:
    """Return sort key for a match, or None if no match.

    Prefer name prefix, then name subsequence, then description substring.
    """
    q = query.lower()
    name = item.name.lower()
    desc = item.description.lower()
    if name.startswith(q):
        return (0, 0, item.name)
    it = iter(name)
    if all(ch in it for ch in q):
        return (1, 0, item.name)
    if q and q in desc:
        return (2, 0, item.name)
    return None


def _at_trigger(document: Document) -> tuple[str, int] | None:
    """If cursor is in an `@…` skill token, return `(filter, start_position)`.

    Activates only when `@` is at the start of input or follows whitespace.
    """
    before = document.text_before_cursor
    if "@" not in before:
        return None
    at = before.rfind("@")
    if at > 0 and not before[at - 1].isspace():
        return None
    fragment = before[at + 1 :]
    if any(ch.isspace() for ch in fragment):
        return None
    if fragment and not re.fullmatch(r"[a-z0-9-]*", fragment):
        return None
    return fragment, -(len(fragment) + 1)


class SkillLexer(Lexer):
    """Highlight known `@skill` tokens in the chat input (accent style)."""

    def __init__(self, cache: SkillCache) -> None:
        self._cache = cache

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        known = self._cache.known_names()

        def get_line(lineno: int) -> StyleAndTextTuples:
            if lineno < 0 or lineno >= len(document.lines):
                return []
            text = document.lines[lineno]
            if not text or not known:
                return [("", text)]
            parts: StyleAndTextTuples = []
            cursor = 0
            for match in SKILL_TOKEN_RE.finditer(text):
                name = match.group(1)
                if name not in known:
                    continue
                start, end = match.start(0), match.end(0)
                if start > cursor:
                    parts.append(("", text[cursor:start]))
                parts.append(("class:skill-token", text[start:end]))
                cursor = end
            if cursor < len(text):
                parts.append(("", text[cursor:]))
            elif not parts:
                parts.append(("", text))
            return parts

        return get_line


class SkillCompleter(Completer):
    """Complete `@skill` tokens from an in-memory catalogue cache."""

    def __init__(self, cache: SkillCache) -> None:
        self._cache = cache

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        _ = complete_event
        trigger = _at_trigger(document)
        if trigger is None:
            return
        query, start_pos = trigger
        usable = [i for i in self._cache.items if not _is_error_item(i)]

        if not query:
            recent = [i for i in usable if i.last_used]
            never = [i for i in usable if not i.last_used]
            recent.sort(key=lambda i: i.last_used or "", reverse=True)
            never.sort(key=lambda i: i.name)
            rows = (recent + never)[:MAX_PICKER_ROWS]
        else:
            ranked: list[tuple[tuple[int, int, str], SkillCatalogItem]] = []
            for item in usable:
                rank = _fuzzy_rank(query, item)
                if rank is not None:
                    ranked.append((rank, item))
            ranked.sort(key=lambda pair: pair[0])
            rows = [item for _, item in ranked[:MAX_PICKER_ROWS]]

        if not rows:
            yield Completion(
                text="",
                start_position=0,
                display=NO_MATCH_TMPL.format(text=sanitize_display_text(query)),
                display_meta="",
                style="class:muted",
            )
            return

        for item in rows:
            desc = sanitize_display_text(item.description)
            if len(desc) > 60:
                desc = desc[:59] + "…"
            yield Completion(
                text=f"@{item.name} ",
                start_position=start_pos,
                display=sanitize_display_text(item.name),
                display_meta=desc,
            )
