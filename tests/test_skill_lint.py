"""CI lint for packaged default skills (size, triggers, lockdown wording)."""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path

import yaml

from aldwin.core.skills import (
    DEFAULT_SKILL_NAMES,
    SKILL_FILENAME,
    SKILL_TEMPLATE_FILENAME,
)

_LOAD_TRIGGER = re.compile(r"\b(?:Load|Use) (when|before|for)\b")
_PROVIDER_DENY = ("openai.com", "anthropic", "tavily", "brave")
# Ban telling the model to retry after refusal; allow "do not retry" / "never retry".
_RETRY_AFTER = re.compile(
    r"(?i)(?<!do not )(?<!don't )(?<!never )\b("
    r"retry|rephrase|try (again|another)|work around|find another way"
    r")\b"
)
_ALDWIN_HOME_ACCESS = re.compile(
    r"(?i)(~/?\.aldwin|ALDWIN_HOME).{0,40}\b(read_file|list_dir|glob|grep|run_command)\b"
    r"|\b(read_file|list_dir|glob|grep|run_command)\b.{0,80}(~/?\.aldwin|ALDWIN_HOME)"
)


def _skill_text(name: str) -> tuple[str, Path]:
    root = Path(str(files("aldwin.core.assets").joinpath("skills") / name))
    template = root / SKILL_TEMPLATE_FILENAME
    path = template if template.is_file() else root / SKILL_FILENAME
    return path.read_text(encoding="utf-8"), path


def test_tc25_default_skill_lint() -> None:
    total_desc = 0
    for name in DEFAULT_SKILL_NAMES:
        text, path = _skill_text(name)
        match = re.match(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", text, re.DOTALL)
        assert match, f"{path}: missing frontmatter"
        meta = yaml.safe_load(match.group(1))
        body = match.group(2).strip()
        assert isinstance(meta, dict)
        assert meta.get("name") == name
        assert "tools" not in meta or not meta.get("tools")
        desc = meta.get("description")
        assert isinstance(desc, str)
        assert len(desc) <= 200
        assert _LOAD_TRIGGER.search(desc), (
            f"{name}: description missing Load/Use when/before/for"
        )
        total_desc += len(desc)
        lines = body.splitlines()
        assert len(lines) <= 80, f"{name}: body has {len(lines)} lines"
        assert len(body) <= 3500, f"{name}: body has {len(body)} chars"
        body_l = body.lower()
        for token in _PROVIDER_DENY:
            if token == "brave" and name == "web-research":
                continue
            if token in body_l and "must use" in body_l:
                raise AssertionError(f"{name}: provider lock-in mention of {token}")
        # using-aldwin may tell the *user* to run tail on the log path.
        if name != "using-aldwin":
            assert not _ALDWIN_HOME_ACCESS.search(body), f"{name}: ~/.aldwin access"
        assert not _RETRY_AFTER.search(body), f"{name}: retry-after-refusal wording"
    assert total_desc <= 1800, f"catalogue descriptions total {total_desc}"


def test_tc26_lint_rejects_aldwin_home_access() -> None:
    bad = 'Run run_command("cat ~/.aldwin/config.yaml") to debug.'
    assert _ALDWIN_HOME_ACCESS.search(bad)


def test_tc27_lint_rejects_retry_after_refusal() -> None:
    bad = "If refused, retry with a rephrased command."
    assert _RETRY_AFTER.search(bad)


def test_connectors_skill_lists_the_catalogue_and_keeps_secrets_out() -> None:
    from aldwin.core.connectors.catalog import load_catalog
    from aldwin.core.skills import default_skill_text

    text = default_skill_text("connectors")
    for entry in load_catalog():
        assert f"- {entry.name} ({entry.title}): {entry.purpose}" in text
    assert re.search(
        r"(?i)never ask the user to type or paste a token, password or api key", text
    )
    assert "$catalogue" not in text
