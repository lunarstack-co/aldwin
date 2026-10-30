"""Deterministic 3-tier approval policy engine, hard floor, and tool wrapper."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import secrets
import sys
import time
import unicodedata
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal

import pathspec
from agentling import Tool
from agentling.tools import ToolCallError

from aldwin.core.approval_log import (
    SECRET_PATTERNS,
    ApprovalLogger,
    LogAnswer,
    LogDecision,
    LogSource,
    LogTier,
    redact_secrets,
)
from aldwin.core.home import (
    FILE_MODE,
    atomic_write,
    cleanup_old_tmp_files,
    format_home_path,
    home_dir,
)
from aldwin.core.tools import ToolOutput, summarize_tool_result
from aldwin.core.workspace import (
    ResolvedPath,
    Workspace,
    format_outside_display,
    is_path_within,
)

logger = logging.getLogger("aldwin.core.policy")

Tier = Literal["auto", "diff", "ask"]
Source = Literal["rule", "grant", "user", "no_approver", "error"]
DecisionKind = Literal["auto", "ask", "diff", "deny"]

MAX_REASON_CHARS: Final[int] = 280
CIRCUIT_BREAKER_LIMIT: Final[int] = 3
TRIM_HEAD_RESERVE_CHARS: Final[int] = 200


class PolicyError(Exception):
    """Raised at startup when a registered tool is missing from `REGISTRY`."""


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """Static policy declaration for one tool in `REGISTRY`."""

    name: str
    tier: Tier | Callable[[dict[str, Any]], Tier]
    path_args: tuple[str, ...] = ()
    writes: bool = False
    never_grant: str | None = None
    parallel_safe: bool = True


@dataclass(frozen=True, slots=True)
class Decision:
    """Deterministic policy decision returned by `decide`."""

    kind: DecisionKind
    source: Source
    reason: str
    grantable: bool = False
    grant_key: str | None = None
    risky: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Structured approval request sent from the policy wrapper to `ask`."""

    tool: str
    tier: Literal["ask", "diff"]
    summary: str
    detail: str | None
    grantable: bool
    grant_key: str | None
    risky: tuple[str, ...]
    digest: str


@dataclass(frozen=True, slots=True)
class ApprovalAnswer:
    """Structured answer returned by `ask`."""

    answer: Literal["yes", "yes_session", "no"]
    source: Literal["user", "no_approver"]
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalPreview:
    """Summary, diff/command detail, and file `st_mtime_ns` built before asking."""

    summary: str
    detail: str | None = None
    mtime_ns: int | None = None
    risky: tuple[str, ...] = ()


PolicyAskFn = Callable[[ApprovalRequest], Awaitable[ApprovalAnswer]]


def remember_tier(args: dict[str, Any]) -> Tier:
    """Return `'diff'` when `remember` has `replace`, else `'auto'`."""
    replace_val = args.get("replace")
    if replace_val is not None and str(replace_val).strip() != "":
        return "diff"
    return "auto"


_SECRET_ASSIGN_DETECT_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Za-z0-9_]*)"
    r"\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s;&|\"']+)",
    re.IGNORECASE,
)
_DIGIT_RUN_RE = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")


def _luhn_ok(digits: str) -> bool:
    """Return True when `digits` passes the Luhn check."""
    if not digits.isdigit() or not (13 <= len(digits) <= 19):
        return False
    total = 0
    reverse = digits[::-1]
    for i, ch in enumerate(reverse):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def looks_like_secret(text: str) -> bool:
    """True when `text` matches shared secret patterns or a Luhn card number."""
    if not text:
        return False
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return True
    if _SECRET_ASSIGN_DETECT_RE.search(text):
        return True
    for match in _DIGIT_RUN_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if _luhn_ok(digits):
            return True
    return False


REGISTRY: Final[dict[str, ToolPolicy]] = {
    "web_search": ToolPolicy(
        name="web_search",
        tier="auto",
        path_args=(),
        writes=False,
        never_grant=None,
        parallel_safe=True,
    ),
    "web_fetch": ToolPolicy(
        name="web_fetch",
        tier="auto",
        path_args=(),
        writes=False,
        never_grant=None,
        parallel_safe=True,
    ),
    "recall": ToolPolicy(
        name="recall",
        tier="auto",
        path_args=(),
        writes=False,
        never_grant=None,
        parallel_safe=True,
    ),
    "remember": ToolPolicy(
        name="remember",
        tier=remember_tier,
        path_args=(),
        writes=False,
        never_grant="memory edits",
        parallel_safe=False,
    ),
    "todo": ToolPolicy(
        name="todo",
        tier="auto",
        path_args=(),
        writes=False,
        never_grant=None,
        parallel_safe=False,
    ),
    "calculate": ToolPolicy(
        name="calculate",
        tier="auto",
        path_args=(),
        writes=False,
        never_grant=None,
        parallel_safe=True,
    ),
    "list_dir": ToolPolicy(
        name="list_dir",
        tier="auto",
        path_args=("path",),
        writes=False,
        never_grant=None,
        parallel_safe=True,
    ),
    "glob": ToolPolicy(
        name="glob",
        tier="auto",
        path_args=("path",),
        writes=False,
        never_grant=None,
        parallel_safe=True,
    ),
    "grep": ToolPolicy(
        name="grep",
        tier="auto",
        path_args=("path",),
        writes=False,
        never_grant=None,
        parallel_safe=True,
    ),
    "read_file": ToolPolicy(
        name="read_file",
        tier="auto",
        path_args=("path",),
        writes=False,
        never_grant=None,
        parallel_safe=True,
    ),
    "read_document": ToolPolicy(
        name="read_document",
        tier="auto",
        path_args=("path",),
        writes=False,
        never_grant=None,
        parallel_safe=True,
    ),
    "edit_file": ToolPolicy(
        name="edit_file",
        tier="diff",
        path_args=("path",),
        writes=True,
        never_grant=None,
        parallel_safe=False,
    ),
    "write_file": ToolPolicy(
        name="write_file",
        tier="diff",
        path_args=("path",),
        writes=True,
        never_grant=None,
        parallel_safe=False,
    ),
    "forget": ToolPolicy(
        name="forget",
        tier="diff",
        path_args=(),
        writes=False,
        never_grant="memory deletes",
        parallel_safe=False,
    ),
    "create_skill": ToolPolicy(
        name="create_skill",
        tier="diff",
        path_args=(),
        writes=False,
        never_grant="new skills",
        parallel_safe=False,
    ),
    "set_persona": ToolPolicy(
        name="set_persona",
        tier="diff",
        path_args=(),
        writes=False,
        never_grant="persona changes",
        parallel_safe=False,
    ),
    "run_command": ToolPolicy(
        name="run_command",
        tier="ask",
        path_args=("cwd",),
        writes=False,
        never_grant="commands",
        parallel_safe=False,
    ),
    "download_file": ToolPolicy(
        name="download_file",
        tier="ask",
        path_args=("dest",),
        writes=True,
        never_grant="downloads",
        parallel_safe=False,
    ),
}

DEFAULT_MAX_OUTPUT_CHARS_BY_TOOL: Final[dict[str, int]] = {
    "read_file": 20_000,
    "read_document": 30_000,
    "web_fetch": 20_000,
    "list_dir": 12_000,
    "glob": 12_000,
    "grep": 12_000,
    "web_search": 10_000,
    "run_command": 10_000,
    "remember": 4_000,
    "recall": 8_000,
    "forget": 4_000,
    "todo": 4_000,
    "calculate": 4_000,
    "edit_file": 4_000,
    "write_file": 4_000,
    "download_file": 1_000,
    "create_skill": 500,
    "set_persona": 4_000,
}

DEFAULT_TOOL_OUTPUT_CAP: Final[int] = 4_000

UNTRUSTED_EXEMPT_TOOLS: Final[frozenset[str]] = frozenset(
    {"todo", "calculate", "final_answer"}
)

BUILD_FILES: Final[frozenset[str]] = frozenset(
    {
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "package.json",
        "Makefile",
        "Dockerfile",
        "Cargo.toml",
        "go.mod",
        "Gemfile",
        "justfile",
    }
)
_BUILD_FILES_FOLDED: Final[frozenset[str]] = frozenset(
    name.casefold() for name in BUILD_FILES
)

ALLOWED_ENV_TEMPLATES: Final[frozenset[str]] = frozenset(
    {".env.example", ".env.sample", ".env.template"}
)

BLOCKED_PATTERNS: Final[tuple[str, ...]] = (
    ".ssh/**",
    "**/.ssh/**",
    ".aws/**",
    "**/.aws/**",
    ".gnupg/**",
    "**/.gnupg/**",
    ".kube/**",
    "**/.kube/**",
    ".azure/**",
    "**/.azure/**",
    ".config/gh/**",
    "**/.config/gh/**",
    ".config/gcloud/**",
    "**/.config/gcloud/**",
    ".docker/config.json",
    "**/.docker/config.json",
    "**/*.pem",
    "*.pem",
    "**/*.key",
    "*.key",
    "**/*.p12",
    "*.p12",
    "**/*.pfx",
    "*.pfx",
    "**/id_rsa*",
    "id_rsa*",
    "**/id_ed25519*",
    "id_ed25519*",
    "**/id_ecdsa*",
    "id_ecdsa*",
    "**/.netrc",
    ".netrc",
    "**/.pgpass",
    ".pgpass",
    "**/.npmrc",
    ".npmrc",
    "**/.pypirc",
    ".pypirc",
    "**/.git-credentials",
    ".git-credentials",
    "**/.env",
    ".env",
    "**/.env.*",
    ".env.*",
    "**/.envrc",
    ".envrc",
    ".aldwin/**",
    "**/.aldwin/**",
)

BLOCKED: Final[pathspec.PathSpec] = pathspec.PathSpec.from_lines(
    "gitignore",
    BLOCKED_PATTERNS,
)

DENIAL_TEXT: Final[dict[str, str]] = {
    "user": (
        "Denied by the user. The tool call did not run. This decision is final: "
        "don't run it again or rephrase it. Ask the user what they want instead."
    ),
    "user_edit": (
        "Denied by the user. The tool call did not run. This decision is final: "
        "don't run it again or rephrase it. Ask the user what they want instead. "
        "The file was not changed."
    ),
    "no_approver": (
        "Not run: no one was available to approve this. "
        "This was not the user's decision."
    ),
    "blocked_path": (
        "Not run: {path} is blocked because it may hold secrets. "
        "This can't be approved."
    ),
    "blocked_git": (
        "Not run: {path} is inside .git and cannot be modified. This can't be approved."
    ),
    "blocked_command": (
        "Not run: this command is never allowed ({rule}). This can't be approved."
    ),
    "policy_error": (
        "Not run: Aldwin's safety check failed for this call, so it was refused."
    ),
    "circuit_breaker": "Stopped after 3 declined actions.",
}

_CMD_PREFIX: Final[str] = (
    r"(?:^|[;&|\n`]|&&|\|\||\$\()\s*"
    r"(?:sudo\s+(?:-[A-Za-z0-9-]+\s+)*|"
    r"env\s+(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+|-[A-Za-z0-9-]+\s+)*)*"
)

_SYSTEM_DIRS_PATTERN: Final[str] = (
    r"(?:bin|boot|dev|etc|lib|opt|sbin|sys|usr|var|System|Library|Applications|Users)"
)
_RM_ROOT_TARGET: Final[str] = (
    r"(?:['\"]?(?:"
    r"/(?:\*+)?(?=$|[\s;&|'\"`])|"
    r"~(?:/|\*+)?(?=$|[\s;&|'\"`])|"
    r"\$HOME(?:/|\b)|"
    r"\$\{HOME\}(?:/|$|[\s;&|'\"`])|"
    r"/" + _SYSTEM_DIRS_PATTERN + r"(?:/|\b)" + r"))"
)
_RM_RECURSIVE_FLAG: Final[str] = r"(?:-[A-Za-z]*[rR][A-Za-z]*|--recursive)\b"

HARDLINE: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "rm_root_or_system",
        re.compile(
            _CMD_PREFIX
            + r"rm\b[^;&|\n`]*\s(?:"
            + _RM_RECURSIVE_FLAG
            + r"[^;&|\n`]*\s+"
            + _RM_ROOT_TARGET
            + r"|"
            + _RM_ROOT_TARGET
            + r"[^;&|\n`]*\s+"
            + _RM_RECURSIVE_FLAG
            + r")"
        ),
    ),
    (
        "mkfs",
        re.compile(_CMD_PREFIX + r"mkfs(?:\.[A-Za-z0-9_-]+)?\b"),
    ),
    (
        "dd_disk",
        re.compile(
            _CMD_PREFIX
            + r"dd\b[^;&|\n`]*\bof=/dev/"
            + r"(?:sd[a-z]\d*|hd[a-z]\d*|nvme\d+n\d+(?:p\d+)?"
            + r"|r?disk\d+(?:s\d+)?|mmcblk\d+(?:p\d+)?)\b"
        ),
    ),
    (
        "redirect_disk",
        re.compile(
            r">>?[ \t]*/dev/"
            r"(?:sd[a-z]\d*|hd[a-z]\d*|nvme\d+n\d+(?:p\d+)?|r?disk\d+(?:s\d+)?|mmcblk\d+(?:p\d+)?)\b"
        ),
    ),
    (
        "fork_bomb",
        re.compile(
            r"(?::|[A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*\{\s*"
            r"(?::|[A-Za-z_][A-Za-z0-9_]*)\s*\|\s*"
            r"(?::|[A-Za-z_][A-Za-z0-9_]*)\s*&\s*\}\s*;\s*"
            r"(?::|[A-Za-z_][A-Za-z0-9_]*)"
        ),
    ),
    (
        "kill_all",
        re.compile(
            _CMD_PREFIX + r"kill\s+(?:-(?:9|KILL|SIGKILL)\s+)?-1(?:\s|$|[;&|`])"
        ),
    ),
    (
        "shutdown",
        re.compile(_CMD_PREFIX + r"shutdown\b"),
    ),
    (
        "reboot",
        re.compile(_CMD_PREFIX + r"reboot\b"),
    ),
    (
        "halt",
        re.compile(_CMD_PREFIX + r"halt\b"),
    ),
    (
        "poweroff",
        re.compile(_CMD_PREFIX + r"poweroff\b"),
    ),
    (
        "init_power",
        re.compile(_CMD_PREFIX + r"(?:tel)?init\s+[06]\b"),
    ),
    (
        "systemctl_power",
        re.compile(_CMD_PREFIX + r"systemctl\s+(?:poweroff|reboot|halt)\b"),
    ),
)

_BLOCKED_CMD_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[\s'\"=/])("
    r"\.aldwin(?:/|\b)|"
    r"\.ssh(?:/|\b)|"
    r"\.aws(?:/|\b)|"
    r"\.gnupg(?:/|\b)|"
    r"\.kube(?:/|\b)|"
    r"\.netrc\b|"
    r"\.git-credentials\b|"
    r"\.pgpass\b|"
    r"\.npmrc\b|"
    r"\.pypirc\b|"
    r"id_rsa\w*|"
    r"id_ed25519\w*|"
    r"id_ecdsa\w*"
    r")",
    re.IGNORECASE,
)

_CMD_ENV_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[\s'\"=/])(\.env(?:\.[A-Za-z0-9_-]+)?)(?=$|[\s'\"/;&|])",
    re.IGNORECASE,
)

RISKY_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("command substitution", re.compile(r"\$\(|`")),
    (
        "pipes into a shell",
        re.compile(
            r"\|\s*(?:(?:sudo|env)\s+)*(?:/bin/|/usr/bin/)?(?:sh|bash|zsh|python3?)\b"
        ),
    ),
    ("network download", re.compile(_CMD_PREFIX + r"(?:curl|wget)\b")),
    (
        "recursive delete",
        re.compile(r"\brm\b[^;&|\n`]*\s(?:-[A-Za-z]*[rR][A-Za-z]*|--recursive)\b"),
    ),
    ("runs as admin", re.compile(r"(?:^|[;&|\n`(\s])sudo\b")),
    (
        "recursive permission change",
        re.compile(
            r"\b(?:chmod|chown)\b[^;&|\n`]*\s(?:-[A-Za-z]*R[A-Za-z]*|--recursive)\b"
        ),
    ),
    (
        "changes git history or remote",
        re.compile(r"\bgit\s+(?:push\b|reset\s+--hard\b|clean\b)"),
    ),
)

_BASE_SAFE_ENV_KEYS: Final[tuple[str, ...]] = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "TERM",
    "TMPDIR",
    "TZ",
    "PWD",
)

_SECRET_ENV_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL",
    re.IGNORECASE,
)

_ANSI_ESCAPE_RE: Final[re.Pattern[str]] = re.compile(
    r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[@-Z\\-_])"
)

_DISPLAY_STRIP_RE: Final[re.Pattern[str]] = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u200e\u200f\u061c\u202a-\u202e\u2066-\u2069]"
)

_UNTRUSTED_TAG_RE: Final[re.Pattern[str]] = re.compile(
    r"<(/?)\s*untrusted\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class StepCallItem:
    """One tool call queued in the current model step."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class PreApprovedOutcome:
    """Result of the step-level batch approval pre-pass for one tool call."""

    decision: Decision
    answer: ApprovalAnswer | None = None
    digest: str | None = None
    mtime_ns: int | None = None
    target_str: str = ""
    denied_text: str | None = None
    denied_summary: str | None = None


class PolicySession:
    """Per-session policy state: workspace, file/folder grants, read mtimes, denials."""

    def __init__(
        self,
        workspace: Workspace | None = None,
        *,
        home: Path | None = None,
        on_circuit_breaker: Callable[[], None] | None = None,
    ) -> None:
        self._home = home
        self.workspace: Workspace = workspace or Workspace(home=home)
        self.grants: set[str] = set()
        self.granted_folders: set[Path] = set()
        self.read_files: dict[Path, int] = {}
        self.turn_denials: int = 0
        self.on_circuit_breaker = on_circuit_breaker

        self.step_calls: list[StepCallItem] = []
        self.step_preapproved: dict[str, PreApprovedOutcome] = {}
        self.step_batch_evaluated: bool = False
        self.step_batch_lock: asyncio.Lock = asyncio.Lock()
        self.active_call_ids: list[tuple[str, str]] = []

    @property
    def home(self) -> Path:
        """Return the resolved Aldwin home directory for this session."""
        return (
            (self._home if self._home is not None else home_dir())
            .expanduser()
            .resolve()
        )

    def mark_read(self, path: str | Path) -> Path:
        """Record `path` and its `st_mtime_ns` in `self.read_files`."""
        return self.workspace.mark_read(path, session=self)

    def add_grant(self, grant_key: str) -> None:
        """Store a `file:<path>` or `folder:<path>` grant in memory."""
        self.grants.add(grant_key)
        if grant_key.startswith("folder:"):
            folder_path = Path(grant_key.removeprefix("folder:"))
            self.granted_folders.add(folder_path)

    def clear_grants(self) -> None:
        """Clear all session file and folder grants."""
        self.grants.clear()
        self.granted_folders.clear()

    def reset_turn(self) -> None:
        """Reset per-turn denial counter and step batch state."""
        self.turn_denials = 0
        self.clear_step_calls()

    def enqueue_step_call(
        self,
        call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> None:
        """Register a `ToolCall` from `ToolCallEvent` before step execution."""
        self.step_calls.append(
            StepCallItem(
                call_id=call_id,
                tool_name=tool_name,
                arguments=dict(arguments),
            )
        )
        self.active_call_ids.append((call_id, tool_name))

    def pop_call_id(
        self,
        tool_name: str = "",
        default: str = "call",
    ) -> str:
        """Return the next queued `call_id` for `tool_name`, or `default`."""
        if tool_name:
            for idx, (cid, tname) in enumerate(self.active_call_ids):
                if tname == tool_name:
                    self.active_call_ids.pop(idx)
                    return cid
        if self.active_call_ids:
            return self.active_call_ids.pop(0)[0]
        return default

    def clear_step_calls(self) -> None:
        """Clear step-level batch state when a model step completes."""
        self.step_calls.clear()
        self.step_preapproved.clear()
        self.step_batch_evaluated = False
        self.active_call_ids.clear()

    def record_denial(
        self,
        *,
        base_text: str,
        base_summary: str,
    ) -> tuple[str, str]:
        """Increment `turn_denials` and return `(result_text, row_summary)`."""
        self.turn_denials += 1
        if self.turn_denials >= CIRCUIT_BREAKER_LIMIT:
            if self.on_circuit_breaker is not None:
                self.on_circuit_breaker()
            cb = DENIAL_TEXT["circuit_breaker"]
            return cb, cb
        return base_text, base_summary


def verify_registered_tools(tools: Iterable[Tool]) -> None:
    """Verify every built tool has a policy entry in `REGISTRY`."""
    for tool_obj in tools:
        if tool_obj.name == "final_answer":
            continue
        if tool_obj.name not in REGISTRY:
            raise PolicyError(
                f'tool "{tool_obj.name}" has no policy entry in core/policy.py'
            )


def sanitize_display_text(text: str) -> str:
    """Strip ANSI escapes, C0/C1 controls, and bidi overrides, and redact secrets."""
    if not text:
        return ""
    no_ansi = _ANSI_ESCAPE_RE.sub("", text)
    no_ctrl = _DISPLAY_STRIP_RE.sub("", no_ansi)
    return redact_secrets(no_ctrl)


def normalise_command(command: str) -> str:
    """Normalise `command` via NFKC, ANSI/control strip, and whitespace collapsing."""
    if not isinstance(command, str) or "\x00" in command:
        raise ValueError("command must be a non-empty string without null bytes")
    no_ansi = _ANSI_ESCAPE_RE.sub("", command)
    no_ctrl = _DISPLAY_STRIP_RE.sub("", no_ansi)
    nfkc = unicodedata.normalize("NFKC", no_ctrl)
    lines = [" ".join(line.split()) for line in nfkc.splitlines()]
    collapsed = "\n".join(line for line in lines if line).strip()
    if not collapsed:
        raise ValueError("command must not be empty")
    return collapsed


def match_hardline(normalised_command: str) -> str | None:
    """Return the matching `HARDLINE` rule name if forbidden."""
    for rule_name, pattern in HARDLINE:
        if pattern.search(normalised_command):
            return rule_name
    return None


def match_blocked_command_path(normalised_command: str) -> str | None:
    """Return the blocked token if `normalised_command` names one."""
    token_match = _BLOCKED_CMD_TOKEN_RE.search(normalised_command)
    if token_match is not None:
        return token_match.group(1)

    for env_match in _CMD_ENV_TOKEN_RE.finditer(normalised_command):
        token = env_match.group(1)
        if token.casefold() not in ALLOWED_ENV_TEMPLATES:
            return token
    return None


def classify_risky_command(normalised_command: str) -> tuple[str, ...]:
    """Return matching risky labels for `normalised_command`."""
    return tuple(
        label for label, pattern in RISKY_PATTERNS if pattern.search(normalised_command)
    )


def is_secret_env_var(name: str) -> bool:
    """Return `True` if `name` looks like a secret environment variable."""
    return bool(_SECRET_ENV_NAME_RE.search(name.strip()))


def clean_env(
    env: Mapping[str, str] | None = None,
    *,
    env_allow: Sequence[str] = (),
    cwd: Path | None = None,
) -> dict[str, str]:
    """Build a clean subprocess environment from an empty base."""
    source = env if env is not None else os.environ
    cleaned: dict[str, str] = {}

    for key in _BASE_SAFE_ENV_KEYS:
        if key in source and not is_secret_env_var(key):
            cleaned[key] = source[key]

    for key, val in source.items():
        if key.startswith("LC_") and not is_secret_env_var(key):
            cleaned[key] = val

    for raw_name in env_allow:
        name = raw_name.strip()
        if not name:
            continue
        if is_secret_env_var(name):
            logger.warning(
                "run_command.env_allow: %s ignored (looks like a secret)",
                name,
            )
            continue
        if name in source:
            cleaned[name] = source[name]

    if cwd is not None:
        cleaned["PWD"] = str(cwd)
    cleaned["TERM"] = "dumb"
    cleaned["PAGER"] = "cat"
    cleaned["GIT_PAGER"] = "cat"
    return cleaned


def _is_aldwin_tmp_path(resolved_path: Path, aldwin_home: Path) -> bool:
    """Return `True` if `resolved_path` is inside `<aldwin_home>/tmp/`."""
    tmp_root = (aldwin_home / "tmp").resolve()
    return resolved_path != tmp_root and is_path_within(resolved_path, tmp_root)


def _is_inside_git_dir(path: Path, raw_path: str) -> bool:
    """Return `True` if `path` or `raw_path` is inside `.git/**`."""
    fold = sys.platform in ("darwin", "win32")
    for part in (*path.parts, *Path(raw_path).parts):
        if (part.casefold() if fold else part) == ".git":
            return True
    return False


def is_build_file(resolved_path: Path) -> bool:
    """Return `True` if `resolved_path` is a build or workflow file."""
    fold = sys.platform in ("darwin", "win32")
    name = resolved_path.name
    if (name.casefold() if fold else name) in (
        _BUILD_FILES_FOLDED if fold else BUILD_FILES
    ):
        return True

    parts = [p.casefold() for p in resolved_path.parts]
    for idx in range(len(parts) - 2):
        if (
            parts[idx] == ".github"
            and parts[idx + 1] == "workflows"
            and parts[idx + 2].endswith((".yml", ".yaml"))
        ):
            return True
    return False


def is_blocked_path(
    resolved_path: Path,
    *,
    raw_path: str = "",
    tool_name: str = "",
    aldwin_home: Path | None = None,
    case_insensitive: bool | None = None,
) -> bool:
    """Return `True` if `resolved_path` or `raw_path` matches `BLOCKED`."""
    fold = (
        sys.platform in ("darwin", "win32")
        if case_insensitive is None
        else case_insensitive
    )
    active_home = (
        (aldwin_home if aldwin_home is not None else home_dir()).expanduser().resolve()
    )

    final_name = resolved_path.name.casefold() if fold else resolved_path.name
    raw_name = (
        Path(raw_path).name.casefold()
        if (raw_path and fold)
        else (Path(raw_path).name if raw_path else "")
    )
    is_env_template = final_name in ALLOWED_ENV_TEMPLATES or (
        bool(raw_name) and raw_name in ALLOWED_ENV_TEMPLATES
    )

    if _is_aldwin_tmp_path(resolved_path, active_home):
        return tool_name != "read_file"

    if is_path_within(resolved_path, active_home, case_insensitive=fold):
        return True

    user_home = Path.home().resolve()
    candidates: list[str] = [resolved_path.as_posix().lstrip("/")]
    if is_path_within(resolved_path, user_home, case_insensitive=fold):
        rel_home = "/".join(resolved_path.parts[len(user_home.parts) :])
        if rel_home:
            candidates.append(rel_home)

    if raw_path:
        cleaned_raw = raw_path.strip()
        if cleaned_raw.startswith("~/"):
            candidates.append(cleaned_raw[2:])
        candidates.append(cleaned_raw.lstrip("/"))

    for cand in candidates:
        probe = cand.casefold() if fold else cand
        if is_env_template and Path(probe).name in ALLOWED_ENV_TEMPLATES:
            parent_str = Path(probe).parent.as_posix()
            if parent_str != "." and BLOCKED.match_file(f"{parent_str}/x"):
                return True
            continue
        if BLOCKED.match_file(probe) or BLOCKED.match_file(f"{probe}/x"):
            return True

    return False


def filter_blocked_paths(
    paths: Iterable[Path | str],
    session: PolicySession,
    *,
    tool_name: str = "glob",
) -> tuple[list[Path], int, str]:
    """Filter blocked entries from `paths` and format the hidden-count footer."""
    allowed: list[Path] = []
    hidden = 0
    for item in paths:
        try:
            resolved = session.workspace.resolve_safe(
                item,
                granted_folders=session.granted_folders,
            )
            if is_blocked_path(
                resolved.path,
                raw_path=str(item),
                tool_name=tool_name,
                aldwin_home=session.home,
            ):
                hidden += 1
                continue
            allowed.append(resolved.path)
        except Exception:
            hidden += 1
    footer = f"({hidden} entries hidden: may hold secrets)" if hidden > 0 else ""
    return allowed, hidden, footer


def _reject_search_pattern(tool_name: str, args: Mapping[str, Any]) -> str | None:
    """Return a denial reason when a glob or grep pattern leaves its folder."""
    if tool_name == "glob":
        raw = args.get("pattern")
    elif tool_name == "grep":
        raw = args.get("glob")
    else:
        return None
    if raw is None:
        return None
    if not isinstance(raw, str):
        return "pattern must stay inside the search folder"
    text = raw.strip()
    if not text:
        return None
    if text.startswith(("/", "~")) or ".." in PurePosixPath(text).parts:
        return "pattern must stay inside the search folder"
    return None


def _validate_args_against_schema(
    args: dict[str, Any],
    schema: Mapping[str, Any] | None,
) -> None:
    """Validate `args` against a JSON Schema parameters object if provided."""
    if not isinstance(args, dict):
        raise ValueError("arguments must be a dict")
    if not schema:
        return
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    for req_key in required:
        if req_key not in args:
            raise ValueError(f"missing required argument: {req_key}")
    if schema.get("additionalProperties") is False and properties:
        for key in args:
            if key not in properties:
                raise ValueError(f"unexpected argument: {key}")


def decide(
    tool_name: str,
    args: dict[str, Any],
    session: PolicySession,
    *,
    schema: Mapping[str, Any] | None = None,
) -> Decision:
    """Evaluate registry, argument, hard-floor, workspace, and grant rules.

    Never raises: any unhandled exception inside `decide()` is caught and
    converted into `Decision(kind="deny", source="error", reason="policy error")`.
    """
    try:
        if not isinstance(tool_name, str) or tool_name not in REGISTRY:
            return Decision(
                kind="deny",
                source="error",
                reason="unknown tool",
            )
        policy = REGISTRY[tool_name]

        _validate_args_against_schema(args, schema)
        pattern_error = _reject_search_pattern(tool_name, args)
        if pattern_error is not None:
            return Decision(
                kind="deny",
                source="error",
                reason=pattern_error,
            )

        resolved_paths: list[tuple[str, str, ResolvedPath]] = []
        for arg_name in policy.path_args:
            raw_val = args.get(arg_name)
            if raw_val is None:
                if arg_name == "path" and not policy.writes:
                    raw_val = "."
                elif arg_name == "cwd":
                    continue
                elif arg_name == "dest" and tool_name == "download_file":
                    # Optional; prepare_approval picks downloads/<name>.
                    continue
                else:
                    raise ValueError(f"missing path argument: {arg_name}")
            if not isinstance(raw_val, (str, Path)):
                raise ValueError(f"path argument {arg_name!r} must be a string")
            resolved = session.workspace.resolve_safe(
                raw_val,
                granted_folders=session.granted_folders,
            )
            resolved_paths.append((arg_name, str(raw_val), resolved))

        normalised_cmd: str | None = None
        if tool_name == "run_command":
            raw_cmd = args.get("command")
            if not isinstance(raw_cmd, str):
                raise ValueError("run_command requires a string 'command'")
            normalised_cmd = normalise_command(raw_cmd)

        for _arg_name, raw_str, res_path in resolved_paths:
            if is_blocked_path(
                res_path.path,
                raw_path=raw_str,
                tool_name=tool_name,
                aldwin_home=session.home,
            ):
                shown = (
                    format_outside_display(res_path.path)
                    if not res_path.inside
                    or res_path.path != (session.workspace.root / raw_str)
                    else res_path.display
                )
                return Decision(
                    kind="deny",
                    source="rule",
                    reason=f"blocked: {shown} may contain secrets",
                )
            if policy.writes and _is_inside_git_dir(res_path.path, raw_str):
                return Decision(
                    kind="deny",
                    source="rule",
                    reason=f"blocked: {res_path.display} is inside .git",
                )

        if tool_name == "run_command" and normalised_cmd is not None:
            hardline_rule = match_hardline(normalised_cmd)
            if hardline_rule is not None:
                return Decision(
                    kind="deny",
                    source="rule",
                    reason=f"blocked command: {hardline_rule}",
                )
            blocked_token = match_blocked_command_path(normalised_cmd)
            if blocked_token is not None:
                return Decision(
                    kind="deny",
                    source="rule",
                    reason=f"blocked: {blocked_token} may contain secrets",
                )
            # Same hard denylist as RunCommandTool (sudo, interactive, bare REPLs).
            from aldwin.core.tools.run_command import check_command_allowed

            refusal = check_command_allowed(
                str(args["command"]),
                apply_denylist=True,
            )
            if refusal is not None:
                return Decision(
                    kind="deny",
                    source="rule",
                    reason=refusal,
                )
            for arg_name, _raw_str, res_path in resolved_paths:
                if arg_name != "cwd":
                    continue
                if not res_path.inside:
                    shown = format_outside_display(res_path.path)
                    return Decision(
                        kind="deny",
                        source="rule",
                        reason=f"cwd outside workspace: {shown}",
                    )

        base_tier: Tier = policy.tier(args) if callable(policy.tier) else policy.tier

        if tool_name == "run_command" and normalised_cmd is not None:
            return Decision(
                kind="ask",
                source="rule",
                reason="Run command",
                grantable=False,
                grant_key=None,
                risky=classify_risky_command(normalised_cmd),
            )

        for _arg_name, _raw_str, res_path in resolved_paths:
            if tool_name == "read_file" and _is_aldwin_tmp_path(
                res_path.path, session.home
            ):
                return Decision(
                    kind="auto",
                    source="rule",
                    reason="aldwin tmp read",
                )

            if not res_path.inside:
                if policy.writes:
                    return Decision(
                        kind="ask",
                        source="rule",
                        reason=f"Edit outside workspace: {res_path.display}",
                        grantable=False,
                        grant_key=None,
                    )
                folder = (
                    res_path.path if res_path.path.is_dir() else res_path.path.parent
                )
                grant_key = f"folder:{folder}"
                if res_path.in_granted_folder or grant_key in session.grants:
                    return Decision(
                        kind="auto",
                        source="grant",
                        reason=f"granted folder: {format_outside_display(folder)}",
                    )
                folder_display = format_outside_display(folder)
                return Decision(
                    kind="ask",
                    source="rule",
                    reason=(
                        f"Read outside workspace: {folder_display} "
                        "(this folder, this session)?"
                    ),
                    grantable=True,
                    grant_key=grant_key,
                )

        if base_tier == "auto":
            return Decision(
                kind="auto",
                source="rule",
                reason="auto tier",
            )

        if base_tier == "diff":
            if policy.never_grant is None and policy.writes and resolved_paths:
                target_res = resolved_paths[0][2]
                if not is_build_file(target_res.path):
                    file_grant_key = f"file:{target_res.path}"
                    if file_grant_key in session.grants:
                        return Decision(
                            kind="auto",
                            source="grant",
                            reason=f"granted file: {target_res.display}",
                            grantable=True,
                            grant_key=file_grant_key,
                        )
                    return Decision(
                        kind="diff",
                        source="rule",
                        reason=f"Edit {target_res.display}",
                        grantable=True,
                        grant_key=file_grant_key,
                    )
            return Decision(
                kind="diff",
                source="rule",
                reason=f"Approve {tool_name}",
                grantable=False,
                grant_key=None,
            )

        return Decision(
            kind="ask",
            source="rule",
            reason=f"Approve {tool_name}",
            grantable=False,
            grant_key=None,
        )
    except Exception as exc:
        logger.exception("Policy decide() failed for tool %r", tool_name, exc_info=exc)
        return Decision(
            kind="deny",
            source="error",
            reason=f"policy error: {type(exc).__name__}",
        )


def canonical_json(obj: Any) -> str:
    """Serialize `obj` as canonical sorted JSON."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_digest(
    tool: str,
    resolved_args: Mapping[str, Any],
    detail: str | None,
) -> str:
    """Compute `sha256` approval digest over tool, resolved args, and diff."""
    diff_hash = hashlib.sha256((detail or "").encode("utf-8")).hexdigest()
    payload = canonical_json(
        {
            "tool": tool,
            "args": dict(resolved_args),
            "diff": diff_hash,
        }
    )
    digest_hex = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"sha256:{digest_hex}"


def wrap_untrusted(
    content: str,
    *,
    tool: str,
    target: str = "",
) -> str:
    """Wrap tool output in `<untrusted id="u-…" source="…">` tags."""
    uid = f"u-{secrets.token_hex(4)}"
    escaped_content = _UNTRUSTED_TAG_RE.sub(r"&lt;\1untrusted", content)
    raw_source = f"{tool}:{target}" if target else tool
    safe_source = (
        raw_source.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", " ")
        .replace("\r", " ")
    )
    return (
        f'<untrusted id="{uid}" source="{safe_source}">\n'
        f"{escaped_content}\n"
        f'</untrusted id="{uid}">'
    )


def trim_tool_output(
    text: str,
    *,
    max_chars: int,
    call_id: str,
    tool: str,
    target: str = "",
    summary: str = "",
    home: Path | None = None,
    wrap: bool = True,
) -> tuple[str, str]:
    """Trim output over `max_chars`, save to `~/.aldwin/tmp/`, and wrap."""
    active_home = (home if home is not None else home_dir()).expanduser().resolve()
    if len(text) <= max_chars:
        wrapped = wrap_untrusted(text, tool=tool, target=target) if wrap else text
        return wrapped, summary

    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", call_id.strip()) or "call"
    tmp_path = active_home / "tmp" / f"{safe_id}.txt"
    atomic_write(tmp_path, text, mode=FILE_MODE)

    display_tmp = format_home_path(tmp_path)
    line_count = max(1, len(text.splitlines()))
    keep_len = max(1, max_chars - TRIM_HEAD_RESERVE_CHARS)
    kept_head = text[:keep_len]

    body = wrap_untrusted(kept_head, tool=tool, target=target) if wrap else kept_head
    footer = (
        f"\n… output trimmed; full text saved to {display_tmp} "
        f"({line_count} lines). Read it with read_file and offset."
    )
    updated_summary = (
        f"{summary} · output trimmed · full text saved"
        if summary
        else "output trimmed · full text saved"
    )
    return f"{body}{footer}", updated_summary


def _tool_call_error_summary(message: str, *, limit: int = 120) -> str:
    """Collapse a `ToolCallError` message to one row-summary line."""
    collapsed = " ".join(message.split())
    if len(collapsed) <= limit:
        return collapsed
    if limit <= 1:
        return collapsed[:limit]
    return f"{collapsed[: limit - 1]}…"


def format_denial_text(
    decision: Decision,
    *,
    is_edit: bool = False,
    user_reason: str | None = None,
    answer_source: Source | None = None,
) -> tuple[str, str]:
    """Return `(model_text, row_summary)` for a denied tool call."""
    effective_source = answer_source or decision.source
    if effective_source == "no_approver":
        return DENIAL_TEXT["no_approver"], "not run: no approver"

    if effective_source == "user":
        base = DENIAL_TEXT["user_edit"] if is_edit else DENIAL_TEXT["user"]
        if user_reason and user_reason.strip():
            trimmed_reason = user_reason.strip()[:MAX_REASON_CHARS]
            base = f"{base}\nUser's reason: {trimmed_reason}"
        return base, "declined"

    if effective_source == "rule":
        reason = decision.reason
        if reason.startswith("blocked command:"):
            rule_name = reason.removeprefix("blocked command:").strip()
            return (
                DENIAL_TEXT["blocked_command"].format(rule=rule_name),
                f"blocked: {rule_name}",
            )
        if reason.endswith("is inside .git"):
            path_part = (
                reason.removeprefix("blocked:").removesuffix("is inside .git").strip()
            )
            return (
                DENIAL_TEXT["blocked_git"].format(path=path_part),
                reason,
            )
        path_part = (
            reason.removeprefix("blocked:").removesuffix("may contain secrets").strip()
        )
        return (
            DENIAL_TEXT["blocked_path"].format(path=path_part),
            reason,
        )

    if decision.reason == "pattern must stay inside the search folder":
        text = (
            "Not run: the search pattern must stay inside its folder. "
            "This can't be approved."
        )
        return text, "blocked: pattern"

    return DENIAL_TEXT["policy_error"], "blocked: safety check failed"


def _is_edit_tool_name(tool_name: str) -> bool:
    """Return `True` if `tool_name` modifies a file, memory note, or persona."""
    policy = REGISTRY.get(tool_name)
    return bool(
        policy is not None
        and (
            policy.writes
            or tool_name
            in ("edit_file", "write_file", "set_persona", "remember", "forget")
        )
    )


def _base_tier_for_tool(tool_name: str, arguments: dict[str, Any]) -> LogTier:
    """Return the base `LogTier` for `tool_name` and `arguments`."""
    policy = REGISTRY.get(tool_name)
    if policy is None:
        return "ask"
    return policy.tier(arguments) if callable(policy.tier) else policy.tier


class PolicyWrappedTool(Tool):
    """Wraps a `Tool` with the 9-step deterministic policy pipeline."""

    def __init__(
        self,
        inner: Tool,
        *,
        session: PolicySession,
        ask: PolicyAskFn,
        approval_log: ApprovalLogger | None = None,
    ) -> None:
        self._inner = inner
        self._session = session
        self._ask = ask
        self._log = approval_log
        self._peer_tools: dict[str, Tool] = {inner.name: self}

        self.name = inner.name
        self.description = inner.description
        self.parameters = inner.parameters
        policy = REGISTRY.get(inner.name)
        self.parallel_safe = policy.parallel_safe if policy is not None else False
        self._exec_timeout = inner.timeout
        # Agentling applies `tool.timeout or agent.tool_timeout` around the
        # *entire* `call()`, which includes waiting on Diff/Ask. A finite
        # value here makes slow human approvals look like ToolTimeoutError
        # (the 60s bug). Disable the outer timer; bound only `forward()`.
        self.timeout = float("inf")
        self._raw_max_output_chars = (
            inner.max_output_chars
            if inner.max_output_chars is not None
            else DEFAULT_MAX_OUTPUT_CHARS_BY_TOOL.get(
                inner.name, DEFAULT_TOOL_OUTPUT_CAP
            )
        )
        # Same trap as timeout: `None` falls back to Agent.max_tool_output_chars,
        # which runs `_truncate_middle` and turns `ToolOutput` into a plain str,
        # dropping `.summary` so the CLI row shows `<untrusted …>` instead.
        # Cap inside `trim_tool_output` only; leave Agentling's outer trim off.
        self.max_output_chars = 1_000_000_000

    @property
    def inner_tool(self) -> Tool:
        """Return the underlying unwrapped `Tool`."""
        return self._inner

    def _is_edit_tool(self) -> bool:
        return _is_edit_tool_name(self.name)

    def _base_tier(self, arguments: dict[str, Any]) -> LogTier:
        return _base_tier_for_tool(self.name, arguments)

    def _resolve_args_and_target(
        self,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], str, Path | None]:
        """Return `(resolved_args_for_digest, target_display, primary_path)`."""
        policy = REGISTRY[self.name]
        resolved_args = dict(arguments)
        target_str = ""
        primary_path: Path | None = None

        for arg_name in policy.path_args:
            raw_val = arguments.get(arg_name)
            if raw_val is None and arg_name == "path" and not policy.writes:
                raw_val = "."
            if isinstance(raw_val, (str, Path)):
                res = self._session.workspace.resolve_safe(
                    raw_val,
                    granted_folders=self._session.granted_folders,
                )
                resolved_args[arg_name] = str(res.path)
                primary_path = res.path
                target_str = res.display

        if not target_str:
            for key in ("command", "url", "query", "name"):
                if key in arguments and isinstance(arguments[key], str):
                    target_str = arguments[key]
                    break
            else:
                target_str = "persona.md" if self.name == "set_persona" else self.name

        return resolved_args, target_str, primary_path

    async def _build_preview(
        self,
        arguments: dict[str, Any],
        decision: Decision,
        primary_path: Path | None,
    ) -> ApprovalPreview:
        """Build `(summary, detail, mtime_ns)` for an `ApprovalRequest`."""
        prepare_fn = getattr(self._inner, "prepare_approval", None)
        if callable(prepare_fn):
            preview = prepare_fn(**arguments)
            if inspect.isawaitable(preview):
                preview = await preview
            return preview

        mtime_ns = (
            primary_path.stat().st_mtime_ns
            if (primary_path is not None and primary_path.exists())
            else None
        )

        if self.name == "run_command":
            cmd = str(arguments.get("command", ""))
            short_cmd = " ".join(cmd.split())
            if len(short_cmd) > 60:
                short_cmd = f"{short_cmd[:59]}…"
            return ApprovalPreview(
                summary=f"Run command: {short_cmd}",
                detail=cmd,
                mtime_ns=mtime_ns,
            )

        return ApprovalPreview(
            summary=decision.reason,
            detail=str(primary_path) if primary_path is not None else decision.reason,
            mtime_ns=mtime_ns,
        )

    def _log_decision(
        self,
        *,
        call_id: str,
        target: str,
        digest: str | None,
        tier: LogTier,
        decision: LogDecision,
        answer: LogAnswer,
        source: LogSource,
        reason: str | None = None,
    ) -> None:
        if self._log is not None:
            self._log.log_decision(
                call_id=call_id,
                tool=self.name,
                target=target,
                digest=digest,
                tier=tier,
                decision=decision,
                answer=answer,
                source=source,
                reason=reason,
            )

    async def evaluate_for_step(
        self,
        call_id: str,
        arguments: dict[str, Any],
    ) -> PreApprovedOutcome:
        """Run steps 1 to 7 for `call_id` and return a `PreApprovedOutcome`."""
        is_edit = self._is_edit_tool()
        base_tier = self._base_tier(arguments)
        decision = decide(
            self.name,
            arguments,
            self._session,
            schema=self.parameters,
        )
        if decision.kind == "deny":
            fallback_target = str(
                arguments.get("path") or arguments.get("command") or self.name
            )
            self._log_decision(
                call_id=call_id,
                target=fallback_target,
                digest=None,
                tier=base_tier,
                decision="blocked",
                answer="none",
                source=decision.source,
                reason=decision.reason,
            )
            denial_msg, row_summary = format_denial_text(decision, is_edit=is_edit)
            final_msg, row_summary = self._session.record_denial(
                base_text=denial_msg,
                base_summary=row_summary,
            )
            return PreApprovedOutcome(
                decision=decision,
                target_str=fallback_target,
                denied_text=final_msg,
                denied_summary=row_summary,
            )

        resolved_args, target_str, primary_path = self._resolve_args_and_target(
            arguments
        )

        if decision.kind == "auto":
            mtime_ns = (
                primary_path.stat().st_mtime_ns
                if (primary_path is not None and primary_path.exists())
                else None
            )
            self._log_decision(
                call_id=call_id,
                target=target_str,
                digest=None,
                tier=base_tier,
                decision="auto",
                answer="none",
                source=decision.source,
                reason=decision.reason,
            )
            return PreApprovedOutcome(
                decision=decision,
                mtime_ns=mtime_ns,
                target_str=target_str,
            )

        try:
            preview = await self._build_preview(arguments, decision, primary_path)
        except ToolCallError as exc:
            message = str(exc)
            return PreApprovedOutcome(
                decision=decision,
                target_str=target_str,
                denied_text=message,
                denied_summary=_tool_call_error_summary(message),
            )
        digest = compute_digest(self.name, resolved_args, preview.detail)
        preview_risky = getattr(preview, "risky", ()) or ()
        merged_risky = tuple(dict.fromkeys((*decision.risky, *preview_risky)))
        req = ApprovalRequest(
            tool=self.name,
            tier=decision.kind,
            summary=preview.summary,
            detail=preview.detail,
            grantable=decision.grantable,
            grant_key=decision.grant_key if decision.grantable else None,
            risky=merged_risky,
            digest=digest,
        )
        ans = await self._ask(req)
        if ans.answer == "yes_session" and decision.grantable and decision.grant_key:
            self._session.add_grant(decision.grant_key)

        if ans.answer == "no":
            self._log_decision(
                call_id=call_id,
                target=target_str,
                digest=digest,
                tier=decision.kind,
                decision="asked",
                answer="no",
                source=ans.source,
                reason=ans.reason or decision.reason,
            )
            denial_msg, row_summary = format_denial_text(
                decision,
                is_edit=is_edit,
                user_reason=ans.reason,
                answer_source=ans.source,
            )
            final_msg, row_summary = self._session.record_denial(
                base_text=denial_msg,
                base_summary=row_summary,
            )
            return PreApprovedOutcome(
                decision=decision,
                answer=ans,
                digest=digest,
                mtime_ns=preview.mtime_ns,
                target_str=target_str,
                denied_text=final_msg,
                denied_summary=row_summary,
            )

        effective_ans: LogAnswer = (
            "yes_session"
            if (ans.answer == "yes_session" and decision.grantable)
            else "yes"
        )
        self._log_decision(
            call_id=call_id,
            target=target_str,
            digest=digest,
            tier=decision.kind,
            decision="asked",
            answer=effective_ans,
            source=ans.source,
            reason=decision.reason,
        )
        return PreApprovedOutcome(
            decision=decision,
            answer=ans,
            digest=digest,
            mtime_ns=preview.mtime_ns,
            target_str=target_str,
        )

    async def call(self, arguments: dict[str, Any]) -> Any:
        """Execute the 9-step deterministic policy pipeline."""
        call_id = self._session.pop_call_id(
            tool_name=self.name,
            default=f"c_{secrets.token_hex(4)}",
        )
        policy = REGISTRY.get(self.name)
        is_edit = self._is_edit_tool()
        base_tier = self._base_tier(arguments) if isinstance(arguments, dict) else "ask"

        try:
            if not isinstance(arguments, dict):
                raise ToolCallError(
                    f"Tool {self.name!r} expected an arguments object, "
                    f"got {type(arguments).__name__}"
                )

            if policy is not None:
                self._inner._validate(arguments)
                validate_args_fn = getattr(self._inner, "validate_arguments", None)
                if callable(validate_args_fn):
                    validate_args_fn(**arguments)

            if (
                len(self._session.step_calls) > 1
                and not self._session.step_batch_evaluated
            ):
                await evaluate_step_batch(
                    self._session,
                    self._peer_tools,
                    self._ask,
                    self._log,
                )

            pre = self._session.step_preapproved.get(call_id)
            if pre is None:
                pre = await self.evaluate_for_step(call_id, arguments)

            if pre.denied_text is not None:
                # Raise so Agentling sets is_error=True; returning ToolOutput(ok=False)
                # looks like a normal tool result and models invent "edit succeeded".
                raise ToolCallError(pre.denied_text)

            resolved_args, target_str, primary_path = self._resolve_args_and_target(
                arguments
            )
            recheck_ok, recheck_out = await self._recheck_before_run(
                call_id=call_id,
                arguments=arguments,
                decision=pre.decision,
                resolved_args=resolved_args,
                target_str=target_str,
                primary_path=primary_path,
                expected_digest=pre.digest,
                expected_mtime_ns=pre.mtime_ns,
                is_edit=is_edit,
            )
            if not recheck_ok and recheck_out is not None:
                raise ToolCallError(str(recheck_out))

        except ToolCallError:
            raise
        except Exception as exc:
            logger.exception(
                "Policy pipeline error in tool %r", self.name, exc_info=exc
            )
            self._log_decision(
                call_id=call_id,
                target=self.name,
                digest=None,
                tier=base_tier,
                decision="blocked",
                answer="none",
                source="error",
                reason=type(exc).__name__,
            )
            err_decision = Decision(
                kind="deny",
                source="error",
                reason=type(exc).__name__,
            )
            denial_msg, row_summary = format_denial_text(err_decision, is_edit=is_edit)
            final_msg, row_summary = self._session.record_denial(
                base_text=denial_msg,
                base_summary=row_summary,
            )
            del row_summary
            raise ToolCallError(final_msg) from exc

        exec_started = time.monotonic()
        if self._exec_timeout is not None and self._exec_timeout != float("inf"):
            raw_output = await asyncio.wait_for(
                self._inner.forward(**arguments),
                timeout=self._exec_timeout,
            )
        else:
            raw_output = await self._inner.forward(**arguments)
        elapsed_ms = max(0, int((time.monotonic() - exec_started) * 1000))

        if isinstance(raw_output, ToolOutput):
            ok = raw_output.ok
            summary = raw_output.summary
            duration_ms = (
                raw_output.duration_ms
                if raw_output.duration_ms is not None
                else elapsed_ms
            )
            raw_text = str(raw_output)
        else:
            raw_text = str(raw_output)
            ok, summary = summarize_tool_result(self.name, raw_text, is_error=False)
            duration_ms = elapsed_ms

        if not ok:
            raise ToolCallError(raw_text)

        should_wrap = self.name not in UNTRUSTED_EXEMPT_TOOLS
        processed_text, updated_summary = trim_tool_output(
            raw_text,
            max_chars=self._raw_max_output_chars,
            call_id=call_id,
            tool=self.name,
            target=target_str,
            summary=summary,
            home=self._session.home,
            wrap=should_wrap,
        )
        return ToolOutput(
            processed_text,
            summary=updated_summary,
            ok=True,
            duration_ms=duration_ms,
        )

    async def _recheck_before_run(
        self,
        *,
        call_id: str,
        arguments: dict[str, Any],
        decision: Decision,
        resolved_args: dict[str, Any],
        target_str: str,
        primary_path: Path | None,
        expected_digest: str | None,
        expected_mtime_ns: int | None,
        is_edit: bool,
    ) -> tuple[bool, ToolOutput | None]:
        """Recompute digest and file mtime before running; re-ask once if changed."""
        if decision.kind not in ("ask", "diff") and decision.source != "grant":
            return True, None

        try:
            current_preview = await self._build_preview(
                arguments, decision, primary_path
            )
        except ToolCallError as exc:
            message = str(exc)
            return False, ToolOutput(
                message,
                summary=_tool_call_error_summary(message),
                ok=False,
                duration_ms=0,
            )
        current_digest = compute_digest(
            self.name,
            resolved_args,
            current_preview.detail,
        )
        current_mtime_ns = current_preview.mtime_ns

        digest_changed = (
            expected_digest is not None and current_digest != expected_digest
        )
        mtime_changed = (
            expected_mtime_ns is not None
            and current_mtime_ns is not None
            and current_mtime_ns != expected_mtime_ns
        )
        if not digest_changed and not mtime_changed:
            return True, None

        reask_tier: Literal["ask", "diff"] = (
            "diff" if decision.kind in ("diff", "auto") else "ask"
        )
        preview_risky = getattr(current_preview, "risky", ()) or ()
        reask_risky = tuple(dict.fromkeys((*decision.risky, *preview_risky)))
        reask_req = ApprovalRequest(
            tool=self.name,
            tier=reask_tier,
            summary=current_preview.summary,
            detail=current_preview.detail,
            grantable=decision.grantable,
            grant_key=decision.grant_key if decision.grantable else None,
            risky=reask_risky,
            digest=current_digest,
        )
        ans = await self._ask(reask_req)
        if ans.answer == "no":
            self._log_decision(
                call_id=call_id,
                target=target_str,
                digest=current_digest,
                tier=reask_tier,
                decision="asked",
                answer="no",
                source=ans.source,
                reason=ans.reason or "changed before run",
            )
            denial_msg, row_summary = format_denial_text(
                decision,
                is_edit=is_edit,
                user_reason=ans.reason,
                answer_source=ans.source,
            )
            final_msg, row_summary = self._session.record_denial(
                base_text=denial_msg,
                base_summary=row_summary,
            )
            return False, ToolOutput(
                final_msg,
                summary=row_summary,
                ok=False,
                duration_ms=0,
            )

        try:
            second_preview = await self._build_preview(
                arguments, decision, primary_path
            )
        except ToolCallError as exc:
            message = str(exc)
            return False, ToolOutput(
                message,
                summary=_tool_call_error_summary(message),
                ok=False,
                duration_ms=0,
            )
        second_digest = compute_digest(
            self.name,
            resolved_args,
            second_preview.detail,
        )
        second_mtime_ns = second_preview.mtime_ns
        if second_digest != current_digest or (
            current_mtime_ns is not None
            and second_mtime_ns is not None
            and second_mtime_ns != current_mtime_ns
        ):
            self._log_decision(
                call_id=call_id,
                target=target_str,
                digest=second_digest,
                tier=reask_tier,
                decision="blocked",
                answer="none",
                source="error",
                reason="file changed twice before run",
            )
            err_dec = Decision(
                kind="deny",
                source="error",
                reason="file changed twice before run",
            )
            denial_msg, row_summary = format_denial_text(err_dec, is_edit=is_edit)
            final_msg, row_summary = self._session.record_denial(
                base_text=denial_msg,
                base_summary=row_summary,
            )
            return False, ToolOutput(
                final_msg,
                summary=row_summary,
                ok=False,
                duration_ms=0,
            )

        self._log_decision(
            call_id=call_id,
            target=target_str,
            digest=current_digest,
            tier=reask_tier,
            decision="asked",
            answer=(
                "yes_session"
                if ans.answer == "yes_session" and decision.grantable
                else "yes"
            ),
            source=ans.source,
            reason="re-approved after change",
        )
        return True, None


async def evaluate_step_batch(
    session: PolicySession,
    tools_by_name: Mapping[str, Tool],
    ask: PolicyAskFn,
    approval_log: ApprovalLogger | None = None,
) -> None:
    """Collect `Diff`/`Ask` approvals for `step_calls` before running."""
    _ = (ask, approval_log)
    async with session.step_batch_lock:
        if session.step_batch_evaluated or len(session.step_calls) <= 1:
            session.step_batch_evaluated = True
            return
        session.step_batch_evaluated = True

        has_interactive = any(
            decide(
                item.tool_name,
                item.arguments,
                session,
                schema=(
                    tools_by_name[item.tool_name].parameters
                    if item.tool_name in tools_by_name
                    else None
                ),
            ).kind
            in ("ask", "diff")
            for item in session.step_calls
            if item.tool_name != "final_answer"
        )
        if not has_interactive:
            return

        batch_rejected_ans: ApprovalAnswer | None = None
        for item in session.step_calls:
            wrapped = tools_by_name.get(item.tool_name)
            if not isinstance(wrapped, PolicyWrappedTool):
                continue

            if batch_rejected_ans is not None:
                is_edit = wrapped._is_edit_tool()
                dec = decide(
                    item.tool_name,
                    item.arguments,
                    session,
                    schema=wrapped.parameters,
                )
                denial_msg, row_summary = format_denial_text(
                    dec,
                    is_edit=is_edit,
                    user_reason=batch_rejected_ans.reason,
                    answer_source=batch_rejected_ans.source,
                )
                final_msg, row_summary = session.record_denial(
                    base_text=denial_msg,
                    base_summary=row_summary,
                )
                wrapped._log_decision(
                    call_id=item.call_id,
                    target=str(
                        item.arguments.get("path")
                        or item.arguments.get("command")
                        or item.tool_name
                    ),
                    digest=None,
                    tier=wrapped._base_tier(item.arguments),
                    decision="asked",
                    answer="no",
                    source=batch_rejected_ans.source,
                    reason=batch_rejected_ans.reason or "batch step declined",
                )
                session.step_preapproved[item.call_id] = PreApprovedOutcome(
                    decision=dec,
                    target_str=item.tool_name,
                    denied_text=final_msg,
                    denied_summary=row_summary,
                )
                continue

            outcome = await wrapped.evaluate_for_step(item.call_id, item.arguments)
            session.step_preapproved[item.call_id] = outcome

            if outcome.answer is not None and outcome.answer.answer == "no":
                batch_rejected_ans = outcome.answer
                call_tool_map = {sc.call_id: sc.tool_name for sc in session.step_calls}
                for prev_id, prev_out in session.step_preapproved.items():
                    if prev_id != item.call_id and prev_out.denied_text is None:
                        prev_tool = call_tool_map.get(prev_id, "")
                        prev_msg, prev_sum = format_denial_text(
                            prev_out.decision,
                            is_edit=_is_edit_tool_name(prev_tool),
                            user_reason=batch_rejected_ans.reason,
                            answer_source=batch_rejected_ans.source,
                        )
                        prev_out.denied_text = prev_msg
                        prev_out.denied_summary = prev_sum


def wrap_tools_with_policy(
    tools: Iterable[Tool],
    *,
    session: PolicySession,
    ask: PolicyAskFn,
    approval_log: ApprovalLogger | None = None,
) -> list[Tool]:
    """Verify `tools` against `REGISTRY` and wrap in `PolicyWrappedTool`."""
    tool_list = list(tools)
    verify_registered_tools(tool_list)
    wrapped_list: list[PolicyWrappedTool] = [
        tool_obj
        if isinstance(tool_obj, PolicyWrappedTool)
        else PolicyWrappedTool(
            tool_obj,
            session=session,
            ask=ask,
            approval_log=approval_log,
        )
        for tool_obj in tool_list
    ]
    peer_map: dict[str, Tool] = {t.name: t for t in wrapped_list}
    for t in wrapped_list:
        t._peer_tools = peer_map
    return list(wrapped_list)


__all__ = [
    "ALLOWED_ENV_TEMPLATES",
    "ApprovalAnswer",
    "ApprovalPreview",
    "ApprovalRequest",
    "BLOCKED",
    "BLOCKED_PATTERNS",
    "BUILD_FILES",
    "CIRCUIT_BREAKER_LIMIT",
    "DEFAULT_MAX_OUTPUT_CHARS_BY_TOOL",
    "DEFAULT_TOOL_OUTPUT_CAP",
    "DENIAL_TEXT",
    "Decision",
    "DecisionKind",
    "HARDLINE",
    "MAX_REASON_CHARS",
    "PolicyAskFn",
    "PolicyError",
    "PolicySession",
    "PolicyWrappedTool",
    "PreApprovedOutcome",
    "REGISTRY",
    "RISKY_PATTERNS",
    "SECRET_PATTERNS",
    "Source",
    "StepCallItem",
    "Tier",
    "ToolPolicy",
    "UNTRUSTED_EXEMPT_TOOLS",
    "canonical_json",
    "classify_risky_command",
    "clean_env",
    "cleanup_old_tmp_files",
    "compute_digest",
    "decide",
    "evaluate_step_batch",
    "filter_blocked_paths",
    "format_denial_text",
    "is_blocked_path",
    "is_build_file",
    "is_secret_env_var",
    "looks_like_secret",
    "match_blocked_command_path",
    "match_hardline",
    "normalise_command",
    "redact_secrets",
    "remember_tier",
    "sanitize_display_text",
    "trim_tool_output",
    "verify_registered_tools",
    "wrap_tools_with_policy",
    "wrap_untrusted",
]
