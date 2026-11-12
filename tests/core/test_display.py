"""Unit tests for `aldwin.core.tools.display` builders and host size caps."""

from __future__ import annotations

import copy
import difflib
from typing import Any

from aldwin.core.tools import ToolOutput
from aldwin.core.tools import display as core_display
from aldwin.protocol import display as proto_display


def test_core_and_protocol_caps_match() -> None:
    """Core plain-dict caps match the protocol display constants."""
    assert core_display.TERMINAL_MAX_LINES == proto_display.TERMINAL_MAX_LINES == 400
    assert core_display.TERMINAL_MAX_BYTES == proto_display.TERMINAL_MAX_BYTES == 65_536
    assert core_display.DIFF_MAX_LINES == proto_display.DIFF_MAX_LINES == 600
    assert core_display.FILES_MAX == proto_display.FILES_MAX == 300
    assert core_display.SEARCH_MAX == proto_display.SEARCH_MAX == 10
    assert core_display.TABLE_MAX_ROWS == proto_display.TABLE_MAX_ROWS == 200
    assert core_display.TABLE_MAX_COLS == proto_display.TABLE_MAX_COLS == 20


def test_terminal_line_cap_boundary() -> None:
    """400 lines are kept without truncation; 401 lines are cut to 400."""
    lines_400 = "".join(f"line {i}\n" for i in range(400))
    lines_401 = "".join(f"line {i}\n" for i in range(401))

    ok_out = core_display.terminal(command="cat f", stdout=lines_400)
    assert ok_out["truncated"] is False
    assert len(ok_out["stdout"].splitlines()) == 400

    cut_out = core_display.terminal(command="cat f", stdout=lines_401)
    assert cut_out["truncated"] is True
    assert len(cut_out["stdout"].splitlines()) == 400

    ok_err = core_display.terminal(command="cat f", stderr=lines_400)
    assert ok_err["truncated"] is False

    cut_err = core_display.terminal(command="cat f", stderr=lines_401)
    assert cut_err["truncated"] is True
    assert len(cut_err["stderr"].splitlines()) == 400


def test_terminal_byte_cap_preserves_multibyte_characters() -> None:
    """70,000-byte output is cut at <= 65,536 UTF-8 bytes without splitting chars."""
    # 'é' is 2 bytes in UTF-8; an odd prefix puts a 2-byte char across byte 65,536.
    big_stdout = "a" + ("é" * 35_000)
    assert len(big_stdout.encode("utf-8")) == 70_001

    built = core_display.terminal(command="echo big", stdout=big_stdout)
    assert built["truncated"] is True
    encoded = built["stdout"].encode("utf-8")
    assert len(encoded) <= 65_536
    assert "\ufffd" not in built["stdout"]
    assert built["stdout"].endswith("é")


def test_diff_builder_from_unified_diff_and_line_cap() -> None:
    """Unified diff parses context, added, and removed lines and caps at 600."""
    before = ["ctx1\n", "old\n", "ctx2\n"]
    after = ["ctx1\n", "new1\n", "new2\n", "new3\n", "ctx2\n"]
    unified = "".join(
        difflib.unified_diff(before, after, fromfile="a/notes.md", tofile="b/notes.md")
    )

    built = core_display.diff(path="notes.md", unified=unified)
    assert built["kind"] == "diff"
    assert built["path"] == "notes.md"
    assert built["additions"] == 3
    assert built["deletions"] == 1
    assert built["truncated"] is False
    assert [row["kind"] for row in built["lines"]] == [
        "context",
        "removed",
        "added",
        "added",
        "added",
        "context",
    ]
    assert proto_display.validate_display(built).kind == "diff"

    after_601 = [f"line {i}\n" for i in range(601)]
    long_unified = list(
        difflib.unified_diff([], after_601, fromfile="/dev/null", tofile="b/big.txt")
    )
    capped = core_display.diff(path="big.txt", unified=long_unified)
    assert capped["additions"] == 601
    assert capped["deletions"] == 0
    assert len(capped["lines"]) == 600
    assert capped["truncated"] is True


def test_files_cap_boundary() -> None:
    """300 file entries keep `truncated=False`; 301 are cut to 300 with `truncated`."""
    entries_300: list[dict[str, Any]] = [
        {"path": f"f_{i}.txt", "kind": "file"} for i in range(300)
    ]
    ok = core_display.files(root="/ws", entries=entries_300)
    assert len(ok["entries"]) == 300
    assert ok["truncated"] is False

    entries_301 = [
        *entries_300,
        {"path": "dir", "kind": "folder", "additions": 2, "deletions": 1},
    ]
    cut = core_display.files(root="/ws", entries=entries_301)
    assert len(cut["entries"]) == 300
    assert cut["truncated"] is True

    with_counts = core_display.files(root="/ws", entries=[entries_301[-1]])
    assert with_counts["entries"] == [
        {"path": "dir", "kind": "folder", "additions": 2, "deletions": 1}
    ]


def test_search_and_table_caps_and_cell_coercion() -> None:
    """Search caps at 10 hits; tables cap at 200x20 and coerce cells to strings."""
    hits_12 = [
        {"title": f"Hit {i}", "url": f"https://example.com/{i}", "snippet": f"s{i}"}
        for i in range(12)
    ]
    srch = core_display.search(query="aldwin", results=hits_12)
    assert srch["kind"] == "search"
    assert srch["query"] == "aldwin"
    assert len(srch["results"]) == 10
    assert srch["truncated"] is True

    headers = ["col", "col", *[f"h{i}" for i in range(2, 25)]]
    rows = [[3, None, 4.0, 1.5, *range(4, 25)] for _ in range(250)]
    tbl = core_display.table(headers=headers, rows=rows)
    assert tbl["kind"] == "table"
    assert len(tbl["columns"]) == 20
    assert len(tbl["rows"]) == 200
    assert tbl["truncated"] is True
    assert tbl["columns"][0] == {"key": "col", "label": "col"}
    assert tbl["columns"][1] == {"key": "col_2", "label": "col"}
    first_row = tbl["rows"][0]
    assert first_row["col"] == "3"
    assert first_row["col_2"] == ""
    assert first_row["h2"] == "4"
    assert first_row["h3"] == "1.5"

    blank_headers = core_display.table(headers=["", ""], rows=[["a", "b", *range(20)]])
    assert [c["label"] for c in blank_headers["columns"]] == ["col1", "col2"]
    assert blank_headers["truncated"] is True


def test_memory_denied_and_grant_exact_shapes_and_tool_output_carrier() -> None:
    """Memory, denied, and grant builders produce exact dicts; ToolOutput keeps it."""
    assert core_display.grant(scope="folder", reach=["/u/Documents/tax"]) == {
        "kind": "grant",
        "scope": "folder",
        "reach": ["/u/Documents/tax"],
    }
    assert core_display.denied(rule="blocked_command", message="No") == {
        "kind": "denied",
        "rule": "blocked_command",
        "message": "No",
    }
    mem = core_display.memory(
        [("fact one", "added"), {"text": "fact two", "change": "removed"}]
    )
    assert mem == {
        "kind": "memory",
        "changes": [
            {"text": "fact one", "change": "added"},
            {"text": "fact two", "change": "removed"},
        ],
    }

    out = ToolOutput("body", summary="sum", ok=True, display=mem)
    assert out.display == mem
    assert copy.deepcopy(out).display == mem
    assert (out + "\nmore").display == mem
