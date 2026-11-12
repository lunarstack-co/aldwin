"""Apps the user picks for one message: validation and the task text."""

from __future__ import annotations

from aldwin.core.connectors.picks import build_picked_apps, resolve_picked_apps

CONNECTED = {
    "linear": "Linear",
    "github": "GitHub",
    "figma": "Figma",
    "notion": "Notion",
}


def test_resolve_dedupes_and_drops_apps_that_are_not_connected() -> None:
    picked, dropped = resolve_picked_apps([" linear", "linear", "", "slack"], CONNECTED)
    assert picked == [("linear", "Linear")]
    assert dropped == [("slack", "unknown")]


def test_resolve_caps_at_three() -> None:
    picked, dropped = resolve_picked_apps(
        ["linear", "github", "figma", "notion"], CONNECTED
    )
    assert [name for name, _ in picked] == ["linear", "github", "figma"]
    assert dropped == [("notion", "limit")]


def test_build_leaves_the_task_alone_without_picks() -> None:
    assert build_picked_apps("hello", []) == "hello"


def test_build_names_the_apps_ahead_of_the_task_and_escapes_titles() -> None:
    text = build_picked_apps("file it", [("linear", "<b>Linear</b>")])
    assert text.startswith("<picked_apps>\n")
    assert "find_connector_tools" in text
    assert "- &lt;b&gt;Linear&lt;/b&gt; (connector: linear)\n" in text
    assert text.endswith("</picked_apps>\n\nfile it")
    assert text.count("<picked_apps>") == 1
