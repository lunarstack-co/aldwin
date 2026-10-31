"""Reading a secret (an API key, a connector token) without showing it."""

from __future__ import annotations

import sys

from prompt_toolkit import PromptSession
from prompt_toolkit.layout.processors import (
    Processor,
    Transformation,
    TransformationInput,
)

from aldwin.cli.theme import PROMPT_STYLE


class HiddenInputProcessor(Processor):
    """Hides input completely, without echoing `*`s that wrap on long tokens."""

    def apply_transformation(
        self,
        transformation_input: TransformationInput,
    ) -> Transformation:
        _ = transformation_input
        return Transformation(
            [],
            source_to_display=lambda _i: 0,
            display_to_source=lambda _i: 0,
        )


async def prompt_input(prompt_text: str, is_password: bool) -> str:
    """Read one line, hiding it when `is_password`; plain `readline` without a TTY."""
    if not sys.stdin.isatty():
        sys.stdout.write(prompt_text)
        sys.stdout.flush()
        line = sys.stdin.readline()
        if line == "":
            raise EOFError
        return line.rstrip("\r\n")
    processors: list[Processor] | None = (
        [HiddenInputProcessor()] if is_password else None
    )
    session: PromptSession[str] = PromptSession(
        style=PROMPT_STYLE,
        input_processors=processors,
    )
    return await session.prompt_async(prompt_text)


async def prompt_secret(prompt_text: str) -> str:
    """Read a secret without showing it."""
    return await prompt_input(prompt_text, True)
