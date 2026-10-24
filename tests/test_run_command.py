"""Unit tests for `RunCommandTool`, denylist, env scrubbing, and timeouts."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from aldwin.core.tools import ToolOutput
from aldwin.core.tools.run_command import (
    build_run_command_tool,
    check_command_allowed,
    scrub_env,
    truncate_command_output,
)


async def test_run_command_approved_executes_in_cwd(tmp_path: Path) -> None:
    """Approved `run_command` runs in `cwd` and returns `exit 0`."""
    (tmp_path / "hello_aldwin.txt").write_text("hi\n", encoding="utf-8")
    ask_calls: list[tuple[str, str]] = []

    async def approve_yes(summary: str, detail: str = "") -> bool:
        ask_calls.append((summary, detail))
        return True

    tool = build_run_command_tool(approve_yes, cwd=tmp_path)
    res = await tool.call({"command": "ls"})

    assert ask_calls == [("Run this command?", "ls")]
    assert isinstance(res, ToolOutput)
    assert res.ok is True
    assert res.summary == "exit 0"
    assert str(res).startswith("exit 0\n")
    assert "hello_aldwin.txt" in str(res)


def test_prepare_approval_includes_resolved_cwd(tmp_path: Path) -> None:
    """Approval preview shows the resolved working directory when cwd is set."""

    async def unused(_summary: str, _detail: str = "") -> bool:
        return True

    tool = build_run_command_tool(unused, cwd=tmp_path)
    sub = tmp_path / "sub"
    sub.mkdir()
    preview = tool.prepare_approval("ls -la", cwd=str(sub))
    assert sub.name in preview.summary
    assert f"$ cd {sub.resolve()}" in preview.detail
    assert preview.detail.endswith("ls -la")


async def test_run_command_declined_skips_execution(tmp_path: Path) -> None:
    """Declined `run_command` skips execution and returns `user declined`."""
    marker = tmp_path / "should_not_be_created.txt"
    ask_calls: list[tuple[str, str]] = []

    async def approve_no(summary: str, detail: str = "") -> bool:
        ask_calls.append((summary, detail))
        return False

    tool = build_run_command_tool(approve_no, cwd=tmp_path)
    cmd = f"touch {marker}"
    res = await tool.call({"command": cmd})

    assert ask_calls == [("Run this command?", cmd)]
    assert not marker.exists()
    assert isinstance(res, ToolOutput)
    assert str(res) == "user declined"
    assert res.summary == "declined"
    assert res.ok is False


async def test_run_command_refuses_sudo_and_interactive_before_ask(
    tmp_path: Path,
) -> None:
    """`sudo`, `su`, `doas`, and interactive commands fail before `ask`."""

    async def fail_if_asked(summary: str, detail: str = "") -> bool:
        raise AssertionError(f"ask() called for refused command: {summary} {detail}")

    tool = build_run_command_tool(fail_if_asked, cwd=tmp_path)

    blocked_commands = [
        ("sudo rm -rf /tmp/foo", "sudo is not allowed"),
        ("/usr/bin/sudo ls", "sudo is not allowed"),
        ("su - root", "su is not allowed"),
        ("doas id", "doas is not allowed"),
        ("env FOO=1 BAR=2 sudo ls", "sudo is not allowed"),
        ("env -i -u HOME sudo whoami", "sudo is not allowed"),
        ("nice -n 10 sudo ls", "sudo is not allowed"),
        ("time -p sudo ls", "sudo is not allowed"),
        ("command -p sudo ls", "sudo is not allowed"),
        ("echo hello && sudo ls", "sudo is not allowed"),
        ("false || doas ls", "doas is not allowed"),
        ("echo ok; su root", "su is not allowed"),
        ("vim notes.txt", "vim is an interactive program"),
        ("vi file.py", "vi is an interactive program"),
        ("nano config.yaml", "nano is an interactive program"),
        ("emacs -nw file.txt", "emacs is an interactive program"),
        ("less README.md", "less is an interactive program"),
        ("more README.md", "more is an interactive program"),
        ("top", "top is an interactive program"),
        ("htop", "htop is an interactive program"),
        ("ssh user@example.com", "ssh is an interactive program"),
        ("df -h | less", "less is an interactive program"),
        ("python", "python with no arguments"),
        ("python3", "python3 with no arguments"),
        ("python3 -i", "python3 with no arguments"),
        ("node", "node with no arguments"),
        ("bash", "bash with no arguments"),
        ("sh", "sh with no arguments"),
        ("zsh", "zsh with no arguments"),
        ("echo $(sudo rm -rf /tmp/x)", "sudo is not allowed"),
        ('echo "`vim notes.txt`"', "vim is an interactive program"),
        ("sh -c 'sudo whoami'", "sudo is not allowed"),
        ("bash -c 'vim notes.txt'", "vim is an interactive program"),
    ]

    for cmd, expected_reason in blocked_commands:
        res = await tool.call({"command": cmd})
        assert isinstance(res, ToolOutput), cmd
        assert res.ok is False, cmd
        assert expected_reason in str(res), (cmd, str(res))
        assert expected_reason in res.summary, (cmd, res.summary)


async def test_run_command_scrubs_secret_env_vars(tmp_path: Path) -> None:
    """`run_command` strips secret env vars and sets non-TTY defaults."""
    test_env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "SAFE_VARIABLE": "visible_value_123",
        "OPENAI_API_KEY": "sk-proj-secret-openai-9999",
        "TAVILY_API_KEY": "tvly-secret-tavily-8888",
        "GITHUB_TOKEN": "ghp_secret_token_7777",
        "AWS_CLIENT_SECRET": "aws_super_secret_6666",
        "DATABASE_PASSWORD": "db_password_5555",
        "MY_PASSWORD_FILE": "pw_file_4444",
        "KEY": "bare_key_secret",
        "TOKEN": "bare_token_secret",
        "SECRET": "bare_secret_value",
    }

    scrubbed = scrub_env(test_env)
    assert scrubbed["SAFE_VARIABLE"] == "visible_value_123"
    assert scrubbed["TERM"] == "dumb"
    assert scrubbed["PAGER"] == "cat"
    assert scrubbed["GIT_PAGER"] == "cat"
    for forbidden in (
        "OPENAI_API_KEY",
        "TAVILY_API_KEY",
        "GITHUB_TOKEN",
        "AWS_CLIENT_SECRET",
        "DATABASE_PASSWORD",
        "MY_PASSWORD_FILE",
        "KEY",
        "TOKEN",
        "SECRET",
    ):
        assert forbidden not in scrubbed

    async def approve_yes(_s: str, _d: str = "") -> bool:
        return True

    tool = build_run_command_tool(approve_yes, cwd=tmp_path, env=test_env)
    res = await tool.call({"command": "env"})

    assert res.ok is True
    out = str(res)
    assert "SAFE_VARIABLE=visible_value_123" in out
    assert "TERM=dumb" in out
    assert "PAGER=cat" in out
    assert "GIT_PAGER=cat" in out
    for secret_val in (
        "OPENAI_API_KEY",
        "sk-proj-secret-openai-9999",
        "TAVILY_API_KEY",
        "tvly-secret-tavily-8888",
        "GITHUB_TOKEN",
        "ghp_secret_token_7777",
        "AWS_CLIENT_SECRET",
        "aws_super_secret_6666",
        "DATABASE_PASSWORD",
        "db_password_5555",
        "MY_PASSWORD_FILE",
    ):
        assert secret_val not in out


async def test_run_command_timeout_kills_process_group(tmp_path: Path) -> None:
    """Timeout kills the entire process group and leaves no zombies."""
    pid_file = tmp_path / "child.pid"

    async def approve_yes(_s: str, _d: str = "") -> bool:
        return True

    tool = build_run_command_tool(approve_yes, cwd=tmp_path, timeout=0.25)
    cmd = f"sleep 60 & echo $! > {pid_file}; wait"

    t0 = time.monotonic()
    res = await tool.call({"command": cmd})
    elapsed = time.monotonic() - t0

    assert elapsed < 1.5
    assert res.ok is False
    assert "timed out after 0.25s" in str(res)
    assert res.summary == "timed out (0.25s)"

    assert pid_file.is_file()
    child_pid = int(pid_file.read_text(encoding="utf-8").strip())
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


async def test_run_command_truncates_output_head_4k_tail_6k(tmp_path: Path) -> None:
    """50,000-char output keeps head 4k and tail 6k with cut marker."""
    raw = ("A" * 4_000) + ("M" * 40_000) + ("Z" * 6_000)
    truncated = truncate_command_output(raw)
    assert truncated == f"{'A' * 4_000}\n… [40000 chars cut] …\n{'Z' * 6_000}"

    async def approve_yes(_s: str, _d: str = "") -> bool:
        return True

    tool = build_run_command_tool(approve_yes, cwd=tmp_path)
    cmd = "python3 -c \"import sys; sys.stdout.write('A'*4000 + 'M'*40000 + 'Z'*6000)\""
    res = await tool.call({"command": cmd})

    assert res.ok is True
    assert res.summary == "exit 0"
    body = str(res).removeprefix("exit 0\n")
    assert body.startswith("A" * 4_000)
    assert "\n… [40000 chars cut] …\n" in body
    assert body.endswith("Z" * 6_000)


async def test_run_command_cancellation_kills_process_group_under_1s(
    tmp_path: Path,
) -> None:
    """Cancelling `RunCommandTool` kills the process group in < 1s."""
    pid_file = tmp_path / "cancel_child.pid"

    async def approve_yes(_s: str, _d: str = "") -> bool:
        return True

    tool = build_run_command_tool(approve_yes, cwd=tmp_path, timeout=60.0)
    cmd = f"sleep 30 & echo $! > {pid_file}; wait"

    task = asyncio.create_task(tool.call({"command": cmd}))
    for _ in range(50):
        if pid_file.is_file() and pid_file.read_text(encoding="utf-8").strip():
            break
        await asyncio.sleep(0.02)

    assert pid_file.is_file()
    child_pid = int(pid_file.read_text(encoding="utf-8").strip())

    t0 = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


async def test_run_command_length_bounds_and_non_interactive_args(
    tmp_path: Path,
) -> None:
    """Length bounds are enforced; `python3 -c` and `sh -c` are allowed."""
    ask_count = 0

    async def approve_yes(_s: str, _d: str = "") -> bool:
        nonlocal ask_count
        ask_count += 1
        return True

    tool = build_run_command_tool(approve_yes, cwd=tmp_path)

    for bad_cmd in ("", "   \n\t ", "bad\x00cmd", "echo " + ("x" * 2_000)):
        out = await tool.call({"command": bad_cmd})
        assert out.ok is False
        assert "Command refused:" in str(out)

    assert ask_count == 0
    assert check_command_allowed(123) == "command must be a valid string"  # type: ignore[arg-type]

    # Non-interactive flags and pipes are permitted.
    res_py = await tool.call({"command": 'python3 -c "print(6 * 7)"'})
    assert res_py.ok is True
    assert str(res_py) == "exit 0\n42"

    res_sh = await tool.call(
        {"command": "sh -c \"printf 'hello\\nworld\\n'\" | head -n 1"}
    )
    assert res_sh.ok is True
    assert str(res_sh) == "exit 0\nhello"

    res_cmd_v = await tool.call({"command": "command -v sh"})
    assert res_cmd_v.ok is True
    assert res_cmd_v.summary == "exit 0"

    # Shell parsing edge cases: quotes, escapes, nice flags, unbalanced quote.
    assert check_command_allowed(r'echo "quoted | && ;" \"escaped\"') is None
    assert check_command_allowed("nice --adjustment=5 ls") is None
    assert check_command_allowed("FOO=1 BAR=2") is None
    assert check_command_allowed("echo 'unbalanced quote | sudo") is None
    assert check_command_allowed("sudo 'unbalanced") == "sudo is not allowed"
    assert (
        truncate_command_output("abcdef", max_chars=2, head_chars=1, tail_chars=0)
        == "a\n… [5 chars cut] …\n"
    )


async def test_run_command_nonzero_exit_and_stderr(tmp_path: Path) -> None:
    """Non-zero exit code and `stderr` return `ok=False` and `exit N`."""

    async def approve_yes(_s: str, _d: str = "") -> bool:
        return True

    tool = build_run_command_tool(approve_yes, cwd=tmp_path)
    res = await tool.call({"command": "sh -c 'echo problem_on_stderr >&2; exit 2'"})

    assert isinstance(res, ToolOutput)
    assert res.ok is False
    assert res.summary == "exit 2"
    assert str(res) == "exit 2\nproblem_on_stderr"

    # Empty output command still formats `exit 0`.
    res_true = await tool.call({"command": "true"})
    assert res_true.ok is True
    assert str(res_true) == "exit 0"


async def test_run_command_approval_wait_does_not_trigger_timeout(
    tmp_path: Path,
) -> None:
    """Waiting on `ask` does not count against execution timeout."""

    async def slow_approve(_s: str, _d: str = "") -> bool:
        await asyncio.sleep(0.25)
        return True

    tool = build_run_command_tool(slow_approve, cwd=tmp_path, timeout=0.15)
    res = await tool.call({"command": "echo approved_after_pause"})

    assert res.ok is True
    assert res.summary == "exit 0"
    assert "approved_after_pause" in str(res)


async def test_run_command_sigkill_escalation_when_sigterm_ignored(
    tmp_path: Path,
) -> None:
    """Process ignoring SIGTERM is killed with SIGKILL after grace period."""

    async def approve_yes(_s: str, _d: str = "") -> bool:
        return True

    tool = build_run_command_tool(approve_yes, cwd=tmp_path, timeout=0.15)
    res = await tool.call(
        {
            "command": (
                'python3 -c "import signal, time; '
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                'time.sleep(30)"'
            )
        }
    )
    assert res.ok is False
    assert "timed out" in str(res)


async def test_run_command_approval_wait_and_post_approval_timeout_in_agent(
    tmp_path: Path,
) -> None:
    """`tool_timeout` skips `ask`; post-approval timeout fires."""
    from agentling import Agent, ToolResultEvent
    from fakes.model import FakeModel, FakeTurn

    async def slow_approve(_s: str, _d: str = "") -> bool:
        await asyncio.sleep(0.15)
        return True

    # 1. Approval wait (0.15s) exceeds Agent.tool_timeout (0.05s) without timing out.
    cmd_tool_ok = build_run_command_tool(slow_approve, cwd=tmp_path, timeout=2.0)
    fake_model_ok = FakeModel(
        turns=[
            FakeTurn.tool_call("run_command", {"command": "echo waited_ok"}),
            FakeTurn.text(["Done."]),
        ]
    )
    agent_ok = Agent(
        model=fake_model_ok,
        tools=[cmd_tool_ok],
        tool_timeout=0.05,
    )
    session_ok = agent_ok.start()
    results_ok = [
        ev.result
        async for ev in session_ok.run("run echo", stream=True, reset=False)
        if isinstance(ev, ToolResultEvent)
    ]
    assert len(results_ok) == 1
    assert results_ok[0].is_error is False
    assert "waited_ok" in results_ok[0].content

    # 2. Post-approval command timeout (0.1s) fires cleanly without ToolTimeoutError.
    async def fast_approve(_s: str, _d: str = "") -> bool:
        return True

    cmd_tool_to = build_run_command_tool(fast_approve, cwd=tmp_path, timeout=0.1)
    fake_model_to = FakeModel(
        turns=[
            FakeTurn.tool_call("run_command", {"command": "sleep 5"}),
            FakeTurn.text(["Timed out."]),
        ]
    )
    agent_to = Agent(
        model=fake_model_to,
        tools=[cmd_tool_to],
        tool_timeout=60.0,
    )
    session_to = agent_to.start()
    results_to = [
        ev.result
        async for ev in session_to.run("run sleep", stream=True, reset=False)
        if isinstance(ev, ToolResultEvent)
    ]
    assert len(results_to) == 1
    assert results_to[0].is_error is False
    assert "Command timed out after 0.1s and was killed." in results_to[0].content
    assert "ToolTimeoutError" not in results_to[0].content


async def test_run_command_clean_env_strips_all_unlisted_and_secret_vars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`run_command('env')` excludes `OPENAI_API_KEY` and `GITHUB_TOKEN`."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test12345678901234567890")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret1234567890123456789012345678")
    monkeypatch.setenv("UNLISTED_PARENT_VAR", "should_not_leak")
    monkeypatch.setenv("ALLOWED_CUSTOM_VAR", "allowed_123")

    async def approve_yes(_s: str, _d: str = "") -> bool:
        return True

    tool = build_run_command_tool(
        approve_yes,
        cwd=tmp_path,
        env_allow=["ALLOWED_CUSTOM_VAR", "GITHUB_TOKEN"],
    )
    res = await tool.call({"command": "env"})
    assert res.ok is True
    out = str(res)
    assert "PATH=" in out
    assert "HOME=" in out
    assert "ALLOWED_CUSTOM_VAR=allowed_123" in out
    assert "OPENAI_API_KEY" not in out
    assert "GITHUB_TOKEN" not in out
    assert "UNLISTED_PARENT_VAR" not in out
