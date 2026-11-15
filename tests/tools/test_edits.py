"""Unit tests for `edit_file` and `write_file`."""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agentling.tools import ToolCallError

from aldwin.core.approval_log import ApprovalLogger
from aldwin.core.policy import (
    DENIAL_TEXT,
    ApprovalAnswer,
    ApprovalRequest,
    PolicySession,
    compute_digest,
    wrap_tools_with_policy,
)
from aldwin.core.tools.edits import (
    EditFileTool,
    WriteFileTool,
    atomic_write_workspace,
    build_edit_tools,
    closest_lines,
    count_plus_minus,
    detect_newline,
    normalize_newlines,
    running_summary,
    unified_create_diff,
    unified_edit_diff,
)
from aldwin.core.tools.files import ReadFileTool, build_file_tools
from aldwin.core.workspace import Workspace

_MINUS = "\u2212"


def _inner(text: str) -> str:
    match = re.search(
        r'<untrusted id="u-[0-9a-f]+" source="[^"]*">\n'
        r"(?P<body>.*)\n"
        r'</untrusted id="u-[0-9a-f]+">',
        text,
        re.DOTALL,
    )
    assert match is not None, text
    return match.group("body")


@pytest.fixture
def edits(tmp_path: Path, aldwin_home: Path) -> SimpleNamespace:
    """Workspace, policy session, and edit tools behind the policy wrapper."""
    root = tmp_path / "ws"
    root.mkdir()
    workspace = Workspace(root, home=aldwin_home)
    session = PolicySession(workspace=workspace, home=aldwin_home)
    log = ApprovalLogger(home=aldwin_home)
    asks: list[ApprovalRequest] = []
    box: dict[str, Any] = {"answer": ApprovalAnswer("yes", "user")}

    async def ask(req: ApprovalRequest) -> ApprovalAnswer:
        asks.append(req)
        return box["answer"]

    raw = [
        *build_file_tools(workspace, session),
        *build_edit_tools(workspace, session),
    ]
    wrapped = wrap_tools_with_policy(
        raw,
        session=session,
        ask=ask,
        approval_log=log,
    )
    return SimpleNamespace(
        root=root,
        home=aldwin_home,
        workspace=workspace,
        session=session,
        log=log,
        asks=asks,
        answer=box,
        tools={tool.name: tool for tool in wrapped},
        tmp=tmp_path,
    )


def _write(path: Path, text: str, *, newline: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if newline is not None:
        text = normalize_newlines(text.replace("\r\n", "\n"), newline)
    path.write_text(text, encoding="utf-8", newline="")


async def _call_fail(tool: Any, args: dict[str, Any]) -> str:
    """Run a wrapped tool that must soft-fail; Agentling sees this as is_error."""
    with pytest.raises(ToolCallError) as caught:
        await tool.call(args)
    return str(caught.value)


async def _mark_read(edits: SimpleNamespace, rel: str) -> None:
    result = await edits.tools["read_file"].call({"path": rel})
    assert result.ok is True, result


def test_detect_newline_crlf_and_lf() -> None:
    assert detect_newline("a\r\nb\r\nc\n") == "\r\n"
    assert detect_newline("a\nb\nc\n") == "\n"
    assert detect_newline("solo") == "\n"


def test_normalize_newlines_and_diff_helpers() -> None:
    assert normalize_newlines("a\nb\n", "\r\n") == "a\r\nb\r\n"
    diff = unified_edit_diff("a.md", "hello\n", "hallo\n")
    assert "--- a/a.md" in diff
    assert "+++ b/a.md" in diff
    added, removed = count_plus_minus(diff)
    assert added == 1 and removed == 1
    create = unified_create_diff("new.md", "one\ntwo\n")
    assert create.startswith("--- /dev/null\n")
    assert "+++ b/new.md\n" in create
    closest = closest_lines("alpha\nbeta\ngamma\n", "bete", n=2)
    assert closest[0][0] == 2


def test_atomic_write_cleans_temp_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "note.txt"
    target.write_text("keep", encoding="utf-8")
    os.chmod(target, 0o640)

    def boom(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="simulated"):
        atomic_write_workspace(target, b"new", preserve_from=target)
    assert target.read_text(encoding="utf-8") == "keep"
    leftovers = list(tmp_path.glob(".note.txt.aldwin-tmp"))
    assert leftovers == []


async def test_edit_without_prior_read(edits: SimpleNamespace) -> None:
    path = edits.root / "notes" / "plan.md"
    _write(path, "draft\n")
    msg = await _call_fail(
        edits.tools["edit_file"],
        {"path": "notes/plan.md", "old": "draft", "new": "final"},
    )
    assert msg == "read the file first: notes/plan.md"
    assert edits.asks == []
    assert path.read_text(encoding="utf-8") == "draft\n"


async def test_unique_edit_approved(edits: SimpleNamespace) -> None:
    path = edits.root / "notes.md"
    _write(path, "You will recieve a key.\n")
    await _mark_read(edits, "notes.md")
    out = await edits.tools["edit_file"].call(
        {
            "path": "notes.md",
            "old": "recieve",
            "new": "receive",
        }
    )
    assert out.ok is True
    assert len(edits.asks) == 1
    req = edits.asks[0]
    assert req.tier == "diff"
    assert req.summary.startswith("Edit notes.md (+1")
    assert "--- a/notes.md" in (req.detail or "")
    assert path.read_text(encoding="utf-8") == "You will receive a key.\n"
    body = _inner(str(out))
    assert body == f"edited notes.md (+1 {_MINUS}1)"
    assert out.summary == f"Edit notes.md · +1 {_MINUS}1"
    assert str(out).count("<untrusted") == 1
    log_path = edits.home / "logs" / "approvals.jsonl"
    lines = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    edit_lines = [row for row in lines if row.get("tool") == "edit_file"]
    assert edit_lines[-1]["answer"] == "yes"


async def test_edit_old_matches_three_times(edits: SimpleNamespace) -> None:
    path = edits.root / "a.md"
    _write(path, "x\nfoo\ny\nfoo\nz\nfoo\n")
    await _mark_read(edits, "a.md")
    msg = await _call_fail(
        edits.tools["edit_file"], {"path": "a.md", "old": "foo", "new": "bar"}
    )
    assert "matches 3 times (lines 2, 4, 6); add more context" in msg
    assert edits.asks == []
    assert path.read_text(encoding="utf-8") == "x\nfoo\ny\nfoo\nz\nfoo\n"


async def test_edit_old_matches_zero(edits: SimpleNamespace) -> None:
    _write(edits.root / "a.md", "alpha\nbeta\ngamma\n")
    await _mark_read(edits, "a.md")
    msg = await _call_fail(
        edits.tools["edit_file"], {"path": "a.md", "old": "bete", "new": "beta"}
    )
    assert msg.startswith("no match for old; closest lines:")
    assert "2: beta" in msg
    assert edits.asks == []


async def test_edit_refuses_stale_mtime(edits: SimpleNamespace) -> None:
    path = edits.root / "a.md"
    _write(path, "hello\n")
    await _mark_read(edits, "a.md")
    path.write_text("hello\nchanged\n", encoding="utf-8")
    msg = await _call_fail(
        edits.tools["edit_file"], {"path": "a.md", "old": "hello", "new": "hi"}
    )
    assert msg == "file changed since last read; read it again"
    assert edits.asks == []


async def test_edit_decline_includes_user_reason(edits: SimpleNamespace) -> None:
    path = edits.root / "a.md"
    _write(path, "colour\n")
    await _mark_read(edits, "a.md")
    edits.answer["answer"] = ApprovalAnswer(
        "no", "user", reason="keep the British spelling"
    )
    msg = await _call_fail(
        edits.tools["edit_file"], {"path": "a.md", "old": "colour", "new": "color"}
    )
    text = msg
    assert DENIAL_TEXT["user_edit"] in text
    assert "User's reason: keep the British spelling" in text
    assert "The file was not changed." in text
    assert path.read_text(encoding="utf-8") == "colour\n"


async def test_write_creates_missing_parents(edits: SimpleNamespace) -> None:
    out = await edits.tools["write_file"].call(
        {
            "path": "out/notes.md",
            "content": "\n".join(f"line {i}" for i in range(14)) + "\n",
        }
    )
    assert out.ok is True
    assert len(edits.asks) == 1
    req = edits.asks[0]
    assert "Create out/notes.md (new file, 14 lines; creates out/)" in req.summary
    assert (req.detail or "").startswith("--- /dev/null")
    path = edits.root / "out" / "notes.md"
    assert path.is_file()
    body = _inner(str(out))
    assert body == "created out/notes.md (14 lines)"
    assert out.summary == "Write out/notes.md · created, 14 lines"


async def test_write_rejects_oversized_content(edits: SimpleNamespace) -> None:
    content = "x" * (1_048_576 + 1)
    msg = await _call_fail(
        edits.tools["write_file"],
        {"path": "big.md", "content": content},
    )
    assert msg == "content too large (limit 1 MB)"
    assert edits.asks == []
    assert not (edits.root / "big.md").exists()


async def test_outside_workspace_edit_never_grantable(edits: SimpleNamespace) -> None:
    outside = edits.tmp / "outside.txt"
    out1 = await edits.tools["write_file"].call(
        {"path": str(outside), "content": "one\n"}
    )
    assert out1.ok is True
    assert len(edits.asks) == 1
    assert "(outside workspace)" in edits.asks[0].summary
    assert edits.asks[0].grantable is False

    edits.answer["answer"] = ApprovalAnswer("yes_session", "user")
    out2 = await edits.tools["write_file"].call(
        {"path": str(outside), "content": "two\n"}
    )
    assert out2.ok is True
    assert len(edits.asks) == 2
    assert edits.asks[1].grantable is False


async def test_edit_blocked_env_file(edits: SimpleNamespace) -> None:
    _write(edits.root / ".env", "SECRET=1\n")
    msg = await _call_fail(
        edits.tools["edit_file"], {"path": ".env", "old": "SECRET=1", "new": "SECRET=2"}
    )
    assert edits.asks == []
    assert "blocked" in msg.lower() or "secrets" in msg.lower()
    assert (edits.root / ".env").read_text(encoding="utf-8") == "SECRET=1\n"


async def test_edit_preserves_crlf(edits: SimpleNamespace) -> None:
    path = edits.root / "win.txt"
    path.write_bytes(b"hello\r\nworld\r\n")
    await _mark_read(edits, "win.txt")
    out = await edits.tools["edit_file"].call(
        {"path": "win.txt", "old": "hello", "new": "hi"}
    )
    assert out.ok is True
    assert path.read_bytes() == b"hi\r\nworld\r\n"


async def test_atomic_replace_failure_leaves_original(
    edits: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = edits.root / "a.md"
    _write(path, "keep\n")
    await _mark_read(edits, "a.md")

    def boom(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    msg = await _call_fail(
        edits.tools["edit_file"], {"path": "a.md", "old": "keep", "new": "gone"}
    )
    assert "failed to write a.md: disk full" in msg
    assert path.read_text(encoding="utf-8") == "keep\n"
    assert list(edits.root.glob(".a.md.aldwin-tmp")) == []


async def test_yes_session_skips_second_ask(edits: SimpleNamespace) -> None:
    path = edits.root / "a.md"
    _write(path, "one\n")
    await _mark_read(edits, "a.md")
    edits.answer["answer"] = ApprovalAnswer("yes_session", "user")
    out1 = await edits.tools["edit_file"].call(
        {"path": "a.md", "old": "one", "new": "two"}
    )
    assert out1.ok is True
    assert len(edits.asks) == 1
    out2 = await edits.tools["edit_file"].call(
        {"path": "a.md", "old": "two", "new": "three"}
    )
    assert out2.ok is True
    assert len(edits.asks) == 1
    assert path.read_text(encoding="utf-8") == "three\n"
    log_path = edits.home / "logs" / "approvals.jsonl"
    rows = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and '"tool": "edit_file"' in line
    ]
    assert any(row.get("source") == "grant" for row in rows)


async def test_build_file_edit_never_grantable(edits: SimpleNamespace) -> None:
    path = edits.root / "pyproject.toml"
    _write(path, "[project]\nname='x'\n")
    await _mark_read(edits, "pyproject.toml")
    edits.answer["answer"] = ApprovalAnswer("yes_session", "user")
    out1 = await edits.tools["edit_file"].call(
        {"path": "pyproject.toml", "old": "name='x'", "new": "name='y'"}
    )
    assert out1.ok is True
    assert edits.asks[0].grantable is False
    out2 = await edits.tools["edit_file"].call(
        {"path": "pyproject.toml", "old": "name='y'", "new": "name='z'"}
    )
    assert out2.ok is True
    assert len(edits.asks) == 2


async def test_git_path_write_blocked(edits: SimpleNamespace) -> None:
    msg = await _call_fail(
        edits.tools["write_file"],
        {"path": ".git/hooks/pre-commit", "content": "#!/bin/sh\n"},
    )
    assert edits.asks == []
    assert "inside .git" in msg
    assert not (edits.root / ".git" / "hooks" / "pre-commit").exists()


async def test_mtime_change_after_yes_fails_closed(edits: SimpleNamespace) -> None:
    """External edit after yes: recheck prepare hits read-before-write and soft-fails.

    Recheck never reaches a second ask because `_require_read` compares the
    session read mtime before a new Diff preview can be built.
    """
    path = edits.root / "a.md"
    _write(path, "v1\n")
    await _mark_read(edits, "a.md")
    asks: list[ApprovalRequest] = []

    async def mutating_ask(req: ApprovalRequest) -> ApprovalAnswer:
        asks.append(req)
        path.write_text("v1-changed\n", encoding="utf-8")
        return ApprovalAnswer("yes", "user")

    wrapped = wrap_tools_with_policy(
        [
            ReadFileTool(edits.workspace, edits.session),
            EditFileTool(edits.workspace, edits.session),
        ],
        session=edits.session,
        ask=mutating_ask,
        approval_log=edits.log,
    )
    tools = {tool.name: tool for tool in wrapped}
    msg = await _call_fail(
        tools["edit_file"],
        {"path": "a.md", "old": "v1", "new": "v2"},
    )
    assert len(asks) == 1
    assert msg == "file changed since last read; read it again"
    assert path.read_text(encoding="utf-8") == "v1-changed\n"


async def test_new_file_digest_covers_all_lines(edits: SimpleNamespace) -> None:
    content = "\n".join(f"line {i}" for i in range(200)) + "\n"
    out = await edits.tools["write_file"].call({"path": "new.md", "content": content})
    assert out.ok is True
    detail = edits.asks[0].detail or ""
    plus_lines = [
        line
        for line in detail.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    assert len(plus_lines) == 200
    assert edits.asks[0].digest.startswith("sha256:")
    digest = compute_digest("write_file", {"path": "new.md"}, detail)
    assert digest.startswith("sha256:")


async def test_symlink_into_ssh_write_blocked(edits: SimpleNamespace) -> None:
    ssh = edits.tmp / ".ssh"
    ssh.mkdir()
    (ssh / "id_rsa").write_text("key", encoding="utf-8")
    link = edits.root / "keys"
    link.symlink_to(ssh)
    await _call_fail(
        edits.tools["write_file"],
        {"path": "keys/stolen.txt", "content": "x\n"},
    )
    assert edits.asks == []
    assert not (ssh / "stolen.txt").exists()


async def test_workspace_switch_clears_file_grants(edits: SimpleNamespace) -> None:
    path = edits.root / "notes.md"
    _write(path, "a\n")
    await _mark_read(edits, "notes.md")
    edits.answer["answer"] = ApprovalAnswer("yes_session", "user")
    assert (
        await edits.tools["edit_file"].call(
            {"path": "notes.md", "old": "a", "new": "b"}
        )
    ).ok
    assert len(edits.asks) == 1

    other = edits.tmp / "other"
    other.mkdir()
    edits.workspace.set_root(other, session=edits.session)
    edits.workspace.set_root(edits.root, session=edits.session)
    await _mark_read(edits, "notes.md")
    out = await edits.tools["edit_file"].call(
        {"path": "notes.md", "old": "b", "new": "c"}
    )
    assert out.ok is True
    assert len(edits.asks) == 2


async def test_written_file_keeps_ansi_and_bidi(edits: SimpleNamespace) -> None:
    path = edits.root / "a.md"
    old = "plain"
    new = "\x1b[31mred\x1b[0m\u202ewrecked"
    _write(path, f"{old}\n")
    await _mark_read(edits, "a.md")
    out = await edits.tools["edit_file"].call({"path": "a.md", "old": old, "new": new})
    assert out.ok is True
    assert path.read_text(encoding="utf-8") == f"{new}\n"


async def test_edit_rejects_empty_or_identical_old_new(edits: SimpleNamespace) -> None:
    _write(edits.root / "a.md", "x\n")
    await _mark_read(edits, "a.md")
    empty = await _call_fail(
        edits.tools["edit_file"],
        {"path": "a.md", "old": "", "new": "y"},
    )
    assert empty == "old is empty"
    same = await _call_fail(
        edits.tools["edit_file"],
        {"path": "a.md", "old": "x", "new": "x"},
    )
    assert same == "old and new must differ"
    assert edits.asks == []


async def test_edit_rejects_non_utf8(edits: SimpleNamespace) -> None:
    path = edits.root / "bin.txt"
    path.write_bytes(b"caf\xe9\n")
    # mark_read via session directly (read_file replaces undecodable bytes)
    edits.session.mark_read(path)
    msg = await _call_fail(
        edits.tools["edit_file"], {"path": "bin.txt", "old": "caf", "new": "CAF"}
    )
    assert msg == "unsupported encoding; edit with run_command instead"
    assert edits.asks == []


async def test_edit_preserves_utf8_bom(edits: SimpleNamespace) -> None:
    path = edits.root / "bom.md"
    path.write_bytes(b"\xef\xbb\xbfhello\n")
    await _mark_read(edits, "bom.md")
    out = await edits.tools["edit_file"].call(
        {"path": "bom.md", "old": "hello", "new": "hi"}
    )
    assert out.ok is True
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert path.read_bytes() == b"\xef\xbb\xbfhi\n"


async def test_write_overwrites_existing_after_read(edits: SimpleNamespace) -> None:
    path = edits.root / "a.md"
    _write(path, "old\n")
    await _mark_read(edits, "a.md")
    out = await edits.tools["write_file"].call({"path": "a.md", "content": "new\n"})
    assert out.ok is True
    assert edits.asks[0].summary.startswith("Overwrite a.md")
    body = _inner(str(out))
    assert body.startswith("wrote a.md")
    assert path.read_text(encoding="utf-8") == "new\n"


async def test_successful_edit_refreshes_read_set(edits: SimpleNamespace) -> None:
    path = edits.root / "a.md"
    _write(path, "one\n")
    await _mark_read(edits, "a.md")
    assert (
        await edits.tools["edit_file"].call(
            {"path": "a.md", "old": "one", "new": "two"}
        )
    ).ok
    out = await edits.tools["edit_file"].call(
        {"path": "a.md", "old": "two", "new": "three"}
    )
    assert out.ok is True
    assert path.read_text(encoding="utf-8") == "three\n"


async def test_partial_read_satisfies_read_before_write(edits: SimpleNamespace) -> None:
    path = edits.root / "a.md"
    _write(path, "\n".join(f"line-{i}" for i in range(1, 21)) + "\n")
    result = await edits.tools["read_file"].call(
        {"path": "a.md", "offset": 1, "limit": 5}
    )
    assert result.ok is True
    out = await edits.tools["edit_file"].call(
        {"path": "a.md", "old": "line-1\n", "new": "line-1x\n"}
    )
    assert out.ok is True
    assert "line-1x" in path.read_text(encoding="utf-8")


async def test_edit_preserves_mode_bits(edits: SimpleNamespace) -> None:
    path = edits.root / "a.md"
    _write(path, "mode\n")
    os.chmod(path, 0o640)
    await _mark_read(edits, "a.md")
    out = await edits.tools["edit_file"].call(
        {"path": "a.md", "old": "mode", "new": "Mode"}
    )
    assert out.ok is True
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


async def test_new_file_uses_lf(edits: SimpleNamespace) -> None:
    out = await edits.tools["write_file"].call({"path": "lf.md", "content": "a\nb\n"})
    assert out.ok is True
    assert (edits.root / "lf.md").read_bytes() == b"a\nb\n"


async def test_edit_running_summary_verbs(edits: SimpleNamespace) -> None:
    assert (
        running_summary("edit_file", {"path": "a.md"}, edits.workspace) == "Edit a.md"
    )
    assert (
        running_summary("write_file", {"path": "b.md"}, edits.workspace) == "Write b.md"
    )


async def test_edit_tools_registered_on_agent(edits: SimpleNamespace) -> None:
    assert "edit_file" in edits.tools
    assert "write_file" in edits.tools
    assert isinstance(edits.tools["edit_file"].inner_tool, EditFileTool)
    assert isinstance(edits.tools["write_file"].inner_tool, WriteFileTool)


async def test_edit_tools_never_call_ask(edits: SimpleNamespace) -> None:
    assert not hasattr(edits.tools["edit_file"].inner_tool, "_ask")
    assert not hasattr(edits.tools["write_file"].inner_tool, "ask")


async def test_aldwin_home_not_writable(edits: SimpleNamespace) -> None:
    target = edits.home / "secrets.yaml"
    await _call_fail(
        edits.tools["write_file"],
        {"path": str(target), "content": "x\n"},
    )
    assert edits.asks == []


async def test_parents_not_created_before_approval(
    edits: SimpleNamespace,
) -> None:
    edits.answer["answer"] = ApprovalAnswer("no", "user")
    await _call_fail(
        edits.tools["write_file"],
        {"path": "newdir/x.md", "content": "hi\n"},
    )
    assert not (edits.root / "newdir").exists()


async def test_multi_match_lists_at_most_ten_lines(edits: SimpleNamespace) -> None:
    lines = "\n".join(["foo"] * 12) + "\n"
    _write(edits.root / "a.md", lines)
    await _mark_read(edits, "a.md")
    msg = await _call_fail(
        edits.tools["edit_file"], {"path": "a.md", "old": "foo", "new": "bar"}
    )
    message = msg
    assert "matches 12 times" in message
    listed = re.search(r"lines ([0-9, ]+)", message)
    assert listed is not None
    numbers = [int(part.strip()) for part in listed.group(1).split(",")]
    assert len(numbers) == 10


async def test_no_bak_sibling(edits: SimpleNamespace) -> None:
    path = edits.root / "a.md"
    _write(path, "x\n")
    await _mark_read(edits, "a.md")
    await edits.tools["edit_file"].call({"path": "a.md", "old": "x", "new": "y"})
    assert not (edits.root / "a.md.bak").exists()


async def test_forgotten_read_needs_a_fresh_read(edits: SimpleNamespace) -> None:
    """Once a read leaves the model's view, editing asks for the file again."""
    path = edits.root / "notes.md"
    _write(path, "draft\n")
    await _mark_read(edits, "notes.md")

    edits.session.forget_reads(["notes.md", "../outside.md", "missing.md"])

    assert path.resolve() not in edits.session.read_files
    assert path.resolve() not in edits.workspace.read_files
    msg = await _call_fail(
        edits.tools["edit_file"],
        {"path": "notes.md", "old": "draft", "new": "final"},
    )
    assert msg == "read the file first: notes.md"
    await _mark_read(edits, "notes.md")
    result = await edits.tools["edit_file"].call(
        {"path": "notes.md", "old": "draft", "new": "final"}
    )
    assert result.ok is True
