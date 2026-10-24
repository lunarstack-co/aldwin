"""`run_command` tool with denylist, approval, secret scrubbing, and timeout."""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import signal
import time
from asyncio.subprocess import Process
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from agentling import Tool

from aldwin.core.tools import AskFn, ToolOutput

if TYPE_CHECKING:
    from aldwin.core.policy import ApprovalPreview

COMMAND_TIMEOUT_SECONDS: Final[float] = 60.0
MAX_COMMAND_CHARS: Final[int] = 2_000
MAX_OUTPUT_CHARS: Final[int] = 10_000
HEAD_OUTPUT_CHARS: Final[int] = 4_000
TAIL_OUTPUT_CHARS: Final[int] = 6_000

_PRIVILEGE_ESCALATION_DENYLIST: Final[frozenset[str]] = frozenset(
    {"sudo", "su", "doas"}
)

_INTERACTIVE_PROGRAM_DENYLIST: Final[frozenset[str]] = frozenset(
    {
        "vim",
        "vi",
        "nvim",
        "view",
        "nano",
        "emacs",
        "pico",
        "less",
        "more",
        "most",
        "top",
        "htop",
        "btop",
        "man",
        "ssh",
        "sftp",
        "ftp",
        "telnet",
    }
)

_BARE_REPL_DENYLIST: Final[frozenset[str]] = frozenset(
    {
        "python",
        "python3",
        "node",
        "bash",
        "sh",
        "zsh",
        "fish",
        "irb",
        "psql",
        "mysql",
        "sqlite3",
    }
)

_WRAPPER_COMMANDS: Final[frozenset[str]] = frozenset(
    {"env", "nice", "time", "command", "nohup"}
)

_ENV_ASSIGN_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_SECRET_ENV_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|_)(?:KEY|TOKEN|SECRET)$|PASSWORD",
    re.IGNORECASE,
)


def _split_shell_segments(command: str) -> list[str]:
    """Split `command` on unquoted `|`, `||`, `&&`, `;`, and newlines."""
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    idx = 0
    length = len(command)

    while idx < length:
        ch = command[idx]
        if escaped:
            current.append(ch)
            escaped = False
            idx += 1
            continue

        if ch == "\\" and quote != "'":
            escaped = True
            current.append(ch)
            idx += 1
            continue

        if quote is not None:
            if ch == quote:
                quote = None
            current.append(ch)
            idx += 1
            continue

        if ch in {"'", '"'}:
            quote = ch
            current.append(ch)
            idx += 1
            continue

        if ch in {";", "\n"}:
            seg = "".join(current).strip()
            if seg:
                segments.append(seg)
            current.clear()
            idx += 1
            continue

        if ch in {"|", "&"}:
            seg = "".join(current).strip()
            if seg:
                segments.append(seg)
            current.clear()
            if idx + 1 < length and command[idx + 1] == ch:
                idx += 2
            else:
                idx += 1
            continue

        current.append(ch)
        idx += 1

    tail = "".join(current).strip()
    if tail:
        segments.append(tail)
    return segments


def _extract_subshells(command: str) -> list[str]:
    """Extract inner command strings from `$(...)` and backtick substitutions."""
    results: list[str] = []
    quote: str | None = None
    escaped = False
    idx = 0
    length = len(command)

    while idx < length:
        ch = command[idx]
        if escaped:
            escaped = False
            idx += 1
            continue
        if ch == "\\" and quote != "'":
            escaped = True
            idx += 1
            continue
        if quote == "'":
            if ch == "'":
                quote = None
            idx += 1
            continue
        if ch == "'" and quote is None:
            quote = "'"
            idx += 1
            continue
        if ch == '"':
            quote = None if quote == '"' else '"'
            idx += 1
            continue
        if ch == "$" and idx + 1 < length and command[idx + 1] == "(":
            depth = 1
            start = idx + 2
            pos = start
            while pos < length and depth > 0:
                if command[pos] == "(":
                    depth += 1
                elif command[pos] == ")":
                    depth -= 1
                pos += 1
            inner = command[start : pos - 1 if depth == 0 else pos].strip()
            if inner:
                results.append(inner)
            idx = pos
            continue
        if ch == "`":
            start = idx + 1
            pos = start
            while pos < length and command[pos] != "`":
                if command[pos] == "\\":
                    pos += 2
                else:
                    pos += 1
            inner = command[start:pos].strip()
            if inner:
                results.append(inner)
            idx = pos + 1
            continue
        idx += 1

    return results


def _unwrap_command_tokens(tokens: list[str]) -> tuple[str, list[str]] | None:
    """Skip leading env assignments and wrapper commands (`env`, `nice`, `time`)."""
    idx = 0
    n = len(tokens)

    while idx < n:
        token = tokens[idx].lstrip("({!")
        if not token:
            idx += 1
            continue

        if _ENV_ASSIGN_RE.match(token):
            idx += 1
            continue

        exe = Path(token).name.lower()
        if exe not in _WRAPPER_COMMANDS:
            return exe, tokens[idx + 1 :]

        idx += 1
        if exe == "env":
            while idx < n:
                arg = tokens[idx]
                if _ENV_ASSIGN_RE.match(arg):
                    idx += 1
                elif arg in {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}:
                    idx += 2
                elif arg.startswith("-"):
                    idx += 1
                else:
                    break
        elif exe == "nice":
            while idx < n:
                arg = tokens[idx]
                if arg in {"-n", "--adjustment"}:
                    idx += 2
                elif arg.startswith("-"):
                    idx += 1
                else:
                    break
        elif exe == "time":
            while idx < n and tokens[idx].startswith("-"):
                idx += 1
        elif exe == "command":
            if idx < n and tokens[idx] in {"-v", "-V"}:
                return "command", tokens[idx:]
            while idx < n and tokens[idx].startswith("-"):
                idx += 1

    return None


def check_command_allowed(
    command: str,
    *,
    apply_denylist: bool = True,
) -> str | None:
    """Validate `command` length, and refuse sudo / interactive / bare REPLs.

    Returns `None` if the command may run, or a concise refusal reason.
    Policy `decide()` and the tool both apply this denylist; do not disable it
    on the production (policy-wrapped) path.
    """
    if not isinstance(command, str) or "\x00" in command:
        return "command must be a valid string"

    stripped = command.strip()
    if not stripped:
        return "command must not be empty"

    if len(command) > MAX_COMMAND_CHARS:
        return f"command exceeds {MAX_COMMAND_CHARS:,} characters"

    if not apply_denylist:
        return None

    segments = _split_shell_segments(stripped)
    if not segments:
        return "command must not be empty"

    for sub in _extract_subshells(stripped):
        segments.extend(_split_shell_segments(sub))

    for segment in segments:
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            tokens = segment.split()

        unwrapped = _unwrap_command_tokens(tokens)
        if unwrapped is None:
            continue

        exe, rest = unwrapped
        if exe in _PRIVILEGE_ESCALATION_DENYLIST:
            return f"{exe} is not allowed"

        if exe in _INTERACTIVE_PROGRAM_DENYLIST:
            return f"{exe} is an interactive program and is not allowed"

        if exe in _BARE_REPL_DENYLIST:
            non_interactive_rest = [
                arg for arg in rest if arg not in {"-i", "--interactive"}
            ]
            if not non_interactive_rest:
                return (
                    f"{exe} with no arguments starts an interactive session "
                    "and is not allowed"
                )
            if exe in {"bash", "sh", "zsh", "fish"} and "-c" in rest:
                c_idx = rest.index("-c")
                if c_idx + 1 < len(rest):
                    nested_refusal = check_command_allowed(rest[c_idx + 1])
                    if nested_refusal is not None:
                        return nested_refusal

    return None


def is_secret_env_var(name: str) -> bool:
    """Return `True` if `name` matches secret patterns (`*_KEY`, `*_TOKEN`, etc.)."""
    return bool(_SECRET_ENV_NAME_RE.search(name.strip()))


def scrub_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy `env`, strip secret variables, and set non-TTY defaults."""
    source = env if env is not None else os.environ
    cleaned = {
        key: value for key, value in source.items() if not is_secret_env_var(key)
    }
    cleaned["TERM"] = "dumb"
    cleaned["PAGER"] = "cat"
    cleaned["GIT_PAGER"] = "cat"
    return cleaned


def truncate_command_output(
    text: str,
    *,
    max_chars: int = MAX_OUTPUT_CHARS,
    head_chars: int = HEAD_OUTPUT_CHARS,
    tail_chars: int = TAIL_OUTPUT_CHARS,
) -> str:
    """Cap output at `max_chars`, keeping `head_chars` and `tail_chars`."""
    if len(text) <= max_chars:
        return text
    kept = head_chars + tail_chars
    cut = max(0, len(text) - kept)
    tail_part = text[-tail_chars:] if tail_chars > 0 else ""
    return f"{text[:head_chars]}\n… [{cut} chars cut] …\n{tail_part}"


async def _terminate_process_group(
    proc: Process,
    *,
    grace_seconds: float = 0.2,
) -> None:
    """Terminate `proc` and its entire process group, leaving no zombie processes."""
    if proc.returncode is not None:
        return

    pid = proc.pid
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except PermissionError:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        return
    except TimeoutError:
        pass

    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass

    try:
        await asyncio.wait_for(proc.wait(), timeout=1.0)
    except TimeoutError:
        pass


class RunCommandTool(Tool):
    """Run a shell command in the launch folder after `y/n` user approval."""

    name = "run_command"
    description = (
        "Run a shell command in the launch directory after asking the user for y/n "
        "approval. Refuses sudo and interactive programs."
    )
    parallel_safe = False
    timeout: float | None = float("inf")
    max_output_chars = 10_500
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "Shell command to execute (1-2,000 characters). "
                    "Never use sudo or interactive editors/shells."
                ),
            },
            "cwd": {
                "type": "string",
                "description": (
                    "Optional working directory for the command "
                    "(defaults to the workspace root)."
                ),
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        ask: AskFn,
        *,
        cwd: Path | Callable[[], Path] | None = None,
        timeout: float = COMMAND_TIMEOUT_SECONDS,
        env: Mapping[str, str] | None = None,
        env_allow: Sequence[str] = (),
        wrapped_by_policy: bool = False,
    ) -> None:
        self._ask = ask
        self._cwd = cwd
        self._timeout = timeout
        self._env = env
        self._env_allow = tuple(env_allow)
        self._wrapped_by_policy = wrapped_by_policy

    def _resolve_work_dir(self, cwd_arg: str | None = None) -> Path:
        base = (
            self._cwd()
            if callable(self._cwd)
            else (self._cwd if self._cwd is not None else Path.cwd())
        ).resolve()
        if not cwd_arg:
            return base
        candidate = Path(cwd_arg).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        return candidate.resolve()

    def prepare_approval(
        self,
        command: str,
        cwd: str | None = None,
    ) -> ApprovalPreview:
        """Build the `ApprovalPreview` for `PolicyWrappedTool`."""
        from aldwin.core.policy import ApprovalPreview

        work_dir = self._resolve_work_dir(cwd)
        short_cmd = " ".join(command.split())
        if len(short_cmd) > 60:
            short_cmd = f"{short_cmd[:59]}…"
        summary = (
            f"Run command in {work_dir.name}: {short_cmd}"
            if short_cmd
            else f"Run command in {work_dir.name}?"
        )
        detail = command if cwd is None else f"$ cd {work_dir}\n{command}"
        return ApprovalPreview(
            summary=summary,
            detail=detail,
            mtime_ns=None,
        )

    async def forward(  # type: ignore[override]
        self,
        command: str,
        cwd: str | None = None,
    ) -> ToolOutput:
        refusal = check_command_allowed(command, apply_denylist=True)
        if refusal is not None:
            return ToolOutput(
                f"Command refused: {refusal}",
                summary=refusal,
                ok=False,
            )

        if not self._wrapped_by_policy:
            approved = await self._ask("Run this command?", command)
            if not approved:
                return ToolOutput(
                    "user declined",
                    summary="declined",
                    ok=False,
                    duration_ms=0,
                )

        exec_started = time.monotonic()
        work_dir = self._resolve_work_dir(cwd)
        if self._env is not None:
            child_env = scrub_env(self._env)
        else:
            from aldwin.core.policy import clean_env

            child_env = clean_env(
                os.environ,
                env_allow=self._env_allow,
                cwd=work_dir,
            )

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(work_dir),
                env=child_env,
                start_new_session=True,
            )
        except OSError as exc:
            reason = " ".join(str(exc).split()) or type(exc).__name__
            return ToolOutput(
                f"command failed to start: {reason}",
                summary=f"failed: {reason}",
                ok=False,
                duration_ms=max(0, int((time.monotonic() - exec_started) * 1000)),
            )

        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=self._timeout,
            )
        except TimeoutError:
            await asyncio.shield(_terminate_process_group(proc))
            return ToolOutput(
                f"Command timed out after {self._timeout:g}s and was killed.",
                summary=f"timed out ({self._timeout:g}s)",
                ok=False,
                duration_ms=max(0, int((time.monotonic() - exec_started) * 1000)),
            )
        except asyncio.CancelledError:
            await asyncio.shield(_terminate_process_group(proc))
            raise

        elapsed_ms = max(0, int((time.monotonic() - exec_started) * 1000))
        exit_code = proc.returncode if proc.returncode is not None else -1
        decoded = stdout_bytes.decode("utf-8", errors="replace").rstrip("\n")
        shown = decoded if self._wrapped_by_policy else truncate_command_output(decoded)
        full_output = f"exit {exit_code}\n{shown}" if shown else f"exit {exit_code}"
        return ToolOutput(
            full_output,
            summary=f"exit {exit_code}",
            ok=(exit_code == 0),
            duration_ms=elapsed_ms,
        )


def build_run_command_tool(
    ask: AskFn,
    *,
    cwd: Path | Callable[[], Path] | None = None,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
    env: Mapping[str, str] | None = None,
    env_allow: Sequence[str] = (),
    wrapped_by_policy: bool = False,
) -> RunCommandTool:
    """Construct the `RunCommandTool` wired with the host's `ask` callback."""
    return RunCommandTool(
        ask=ask,
        cwd=cwd,
        timeout=timeout,
        env=env,
        env_allow=env_allow,
        wrapped_by_policy=wrapped_by_policy,
    )


__all__ = [
    "COMMAND_TIMEOUT_SECONDS",
    "HEAD_OUTPUT_CHARS",
    "MAX_COMMAND_CHARS",
    "MAX_OUTPUT_CHARS",
    "RunCommandTool",
    "TAIL_OUTPUT_CHARS",
    "build_run_command_tool",
    "check_command_allowed",
    "is_secret_env_var",
    "scrub_env",
    "truncate_command_output",
]
