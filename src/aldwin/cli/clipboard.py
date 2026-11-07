"""Put text on the system clipboard from the terminal.

OSC 52 comes first because it also works over SSH, where the copy has to reach
the user's own machine. Local clipboard programs are the fallback.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from typing import IO, Final

MAX_OSC52_BYTES: Final[int] = 100 * 1024
TOOL_TIMEOUT_S: Final[float] = 5.0

# Terminals known to ignore OSC 52, so a fallback is tried instead.
_NO_OSC52_TERM_PROGRAMS: Final[frozenset[str]] = frozenset({"Apple_Terminal"})

# Clipboard programs need to find the display and decode UTF-8, and nothing else.
_TOOL_ENV_KEYS: Final[tuple[str, ...]] = (
    "PATH",
    "HOME",
    "DISPLAY",
    "XAUTHORITY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)

Runner = Callable[..., subprocess.CompletedProcess[bytes]]
Which = Callable[[str], str | None]


def osc52_sequence(text: str) -> str | None:
    """Return the OSC 52 escape for `text`, or `None` if it's over the size cap."""
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    if len(payload) > MAX_OSC52_BYTES:
        return None
    return f"\x1b]52;c;{payload}\x07"


def osc52_supported(*, is_terminal: bool, environ: Mapping[str, str]) -> bool:
    """Guess whether the terminal accepts OSC 52 (it never replies, so it's a guess)."""
    if not is_terminal:
        return False
    if environ.get("TERM", "") in ("", "dumb"):
        return False
    if environ.get("TERM_PROGRAM", "") in _NO_OSC52_TERM_PROGRAMS:
        return False
    # VTE terminals (GNOME Terminal, Tilix) drop OSC 52. Over SSH this variable
    # belongs to the remote side, so it doesn't describe the user's terminal.
    if environ.get("VTE_VERSION") and not environ.get("SSH_CONNECTION"):
        return False
    return True


def fallback_commands(platform: str) -> list[list[str]]:
    """Clipboard programs to try, in order, for this operating system."""
    if platform == "darwin":
        return [["pbcopy"]]
    return [["wl-copy"], ["xclip", "-selection", "clipboard"]]


def _tool_env(environ: Mapping[str, str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _TOOL_ENV_KEYS:
        value = environ.get(key)
        if value:
            env[key] = value
    if not any(key in env for key in ("LANG", "LC_ALL", "LC_CTYPE")):
        # pbcopy reads stdin in the locale's encoding; without one it mangles UTF-8.
        env["LC_CTYPE"] = "UTF-8"
    return env


def _run_tool(
    argv: Sequence[str],
    text: str,
    *,
    environ: Mapping[str, str],
    which: Which,
    run: Runner,
) -> bool:
    program = which(argv[0])
    if program is None:
        return False
    try:
        # Output goes to /dev/null: xclip and wl-copy leave a child running to
        # own the selection, and a captured pipe would wait for it to exit.
        result = run(
            [program, *argv[1:]],
            input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_tool_env(environ),
            timeout=TOOL_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def copy_to_clipboard(
    text: str,
    *,
    stream: IO[str],
    is_terminal: bool,
    environ: Mapping[str, str] | None = None,
    platform: str | None = None,
    which: Which = shutil.which,
    run: Runner = subprocess.run,
) -> str | None:
    """Copy `text` and return how it was copied (`"osc52"` or a program name).

    Returns `None` when no method worked.
    """
    env = os.environ if environ is None else environ
    if osc52_supported(is_terminal=is_terminal, environ=env):
        sequence = osc52_sequence(text)
        if sequence is not None:
            try:
                stream.write(sequence)
                stream.flush()
            except OSError:
                pass
            else:
                return "osc52"

    for argv in fallback_commands(platform or sys.platform):
        if _run_tool(argv, text, environ=env, which=which, run=run):
            return argv[0]
    return None
