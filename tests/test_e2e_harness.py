"""Offline regression tests for the terminal-driven e2e harness."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable, Iterator

import pexpect
import pytest
from e2e.harness import PROMPT_PATTERN, AldwinSpawn
from e2e.test_demos import _drive


@pytest.fixture
def spawn_script() -> Iterator[Callable[[str], AldwinSpawn]]:
    children: list[AldwinSpawn] = []

    def spawn(script: str) -> AldwinSpawn:
        driver = AldwinSpawn.__new__(AldwinSpawn)
        driver._pexpect = pexpect
        driver._buf = []
        driver.child = pexpect.spawn(
            sys.executable, ["-u", "-c", script], encoding="utf-8", timeout=2
        )
        children.append(driver)
        return driver

    yield spawn
    for driver in children:
        driver.child.close(force=True)


@pytest.mark.parametrize(
    "summary",
    [
        "  ✻ Worked for 0s",
        "  * Worked for 12s · 1 tool · 1.1k tokens",
        "\x1b[35m  ✻ \x1b[0m\x1b[90mWorked for 4s\x1b[0m",
    ],
)
def test_wait_done_matches_current_summary(
    spawn_script: Callable[[str], AldwinSpawn], summary: str
) -> None:
    driver = spawn_script(f"print('answer'); print({summary!r}); print('❯ ', end='')")
    text = driver.wait_done(timeout=2)
    assert text.count("answer") == 1
    assert "Worked for" in text
    driver.child.expect(PROMPT_PATTERN, timeout=2)


@pytest.mark.parametrize(
    "output",
    [
        "partial answer",
        "❯ ",
        "> ",
        "✓ done",
        "done ·",
        "› ",
        "You:",
        "(stopped)",
        "Here is an example: ✻ Worked for 1s",
        "  ✻ Worked for ",
    ],
)
def test_wait_done_fails_on_premature_eof(
    spawn_script: Callable[[str], AldwinSpawn], output: str
) -> None:
    driver = spawn_script(f"print({output!r})")
    with pytest.raises(AssertionError, match="exited before the end-of-turn"):
        driver.wait_done(timeout=2)


def test_wait_done_preserves_timeout(
    spawn_script: Callable[[str], AldwinSpawn],
) -> None:
    driver = spawn_script("print('❯ ', flush=True); input()")
    with pytest.raises(pexpect.TIMEOUT):
        driver.wait_done(timeout=0.2)


@pytest.mark.parametrize("prompt", ["❯ ", "> "])
def test_drive_consumes_completion_once(
    spawn_script: Callable[[str], AldwinSpawn], prompt: str
) -> None:
    driver = spawn_script(
        f"print({prompt!r}, end='', flush=True); input(); "
        "print('answer'); print('  ✻ Worked for 1s'); "
        f"print({prompt!r}, end='', flush=True); input()"
    )
    text = _drive(driver, "hello", timeout=2)
    assert "answer" in text
    assert text.count("Worked for") == 1
    driver.child.expect(PROMPT_PATTERN, timeout=2)


def test_drive_preserves_output_before_approval(
    spawn_script: Callable[[str], AldwinSpawn], monkeypatch: pytest.MonkeyPatch
) -> None:
    driver = spawn_script(
        "print('❯ ', end='', flush=True); input(); "
        "print('before approval'); print('Allow', flush=True); input(); "
        "print('after approval'); print('  ✻ Worked for 1s')"
    )
    monkeypatch.setattr(driver, "answer", driver.send_line)
    text = _drive(driver, "hello", timeout=2)
    assert text.count("before approval") == 1
    assert text.count("after approval") == 1
    assert text.count("Worked for") == 1


@pytest.mark.parametrize("prompt", ["❯ ", "> "])
def test_prompt_pattern_matches_current_prompts(prompt: str) -> None:
    assert re.fullmatch(PROMPT_PATTERN, prompt)


@pytest.mark.parametrize("prompt", ["› ", "You:"])
def test_prompt_pattern_rejects_old_prompts(prompt: str) -> None:
    assert re.search(PROMPT_PATTERN, prompt) is None
