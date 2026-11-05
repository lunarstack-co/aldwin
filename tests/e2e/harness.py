"""pexpect harness for E2E demos against the installed `aldwin` binary."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from canary import CANARY

_KEY_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,})")
PROMPT_PATTERN = r"❯ |> "
# Rich can change colour between the summary glyph and its text.
DONE_PATTERN = (
    r"(?:^|[\r\n])(?:\x1b\[[0-9;]*m)*  [✻*] "
    r"(?:\x1b\[[0-9;]*m)*Worked for \d+s[^\r\n]*"
)


def mask_secrets(text: str) -> str:
    """Mask API keys and the canary in transcripts."""
    cleaned = _KEY_RE.sub("sk-‹redacted›", text)
    return cleaned.replace(CANARY, "‹canary›")


def write_transcript(name: str, text: str, *, out_dir: Path | None = None) -> Path:
    """Write a cleaned transcript under `tests/e2e/out/<name>.txt`."""
    root = out_dir or Path(__file__).resolve().parent / "out"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}.txt"
    path.write_text(mask_secrets(text), encoding="utf-8")
    return path


class AldwinSpawn:
    """Drive an installed `aldwin` binary with pexpect."""

    def __init__(
        self,
        home: Path,
        workspace: Path,
        *,
        bin: str = "aldwin",
        env: dict[str, str] | None = None,
    ) -> None:
        import pexpect

        self._pexpect = pexpect
        self.home = home
        self.workspace = workspace
        self.bin = shutil.which(bin) or bin
        child_env = os.environ.copy()
        if env:
            child_env.update(env)
        child_env["ALDWIN_HOME"] = str(home)
        child_env["HOME"] = str(home.parent / "user")
        Path(child_env["HOME"]).mkdir(parents=True, exist_ok=True)
        self.child = pexpect.spawn(
            self.bin,
            ["--workspace", str(workspace)],
            env=child_env,
            encoding="utf-8",
            timeout=60,
        )
        self._buf: list[str] = []

    def send_line(self, text: str) -> None:
        self.child.sendline(text)

    def expect_approval(self, timeout: float = 60) -> None:
        self.child.expect(r"\[y/a/N\]|Allow|Approve|Run this", timeout=timeout)

    def answer(self, key: str) -> None:
        self.child.send(key)

    def wait_done(self, timeout: float = 180) -> str:
        try:
            self.child.expect(DONE_PATTERN, timeout=timeout)
        except self._pexpect.EOF as exc:
            raise AssertionError(
                "Aldwin exited before the end-of-turn 'Worked for' summary.\n"
                + mask_secrets(self.child.before or "")
            ) from exc
        return self.capture_output()

    def capture_output(self) -> str:
        """Record the latest expect match and return the cleaned transcript."""
        before = self.child.before or ""
        after = self.child.after or ""
        text = f"{before}{after}"
        self._buf.append(text)
        return mask_secrets("".join(self._buf))

    def close(self) -> None:
        if self.child.isalive():
            self.child.sendline("/exit")
            try:
                self.child.expect(self._pexpect.EOF, timeout=5)
            except self._pexpect.TIMEOUT:
                self.child.terminate(force=True)
