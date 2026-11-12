"""Plain-dict builders for structured tool displays, with host size caps."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

TERMINAL_MAX_LINES: Final[int] = 400
TERMINAL_MAX_BYTES: Final[int] = 65_536
DIFF_MAX_LINES: Final[int] = 600
FILES_MAX: Final[int] = 300
SEARCH_MAX: Final[int] = 10
TABLE_MAX_ROWS: Final[int] = 200
TABLE_MAX_COLS: Final[int] = 20


def _cap_terminal_stream(text: str) -> tuple[str, bool]:
    """Cap one terminal stream at `TERMINAL_MAX_LINES` and `TERMINAL_MAX_BYTES`."""
    if not text:
        return "", False

    truncated = False
    lines = text.splitlines(keepends=True)
    if len(lines) > TERMINAL_MAX_LINES:
        text = "".join(lines[:TERMINAL_MAX_LINES])
        truncated = True

    encoded = text.encode("utf-8")
    if len(encoded) > TERMINAL_MAX_BYTES:
        text = encoded[:TERMINAL_MAX_BYTES].decode("utf-8", errors="ignore")
        truncated = True

    return text, truncated


def terminal(
    *,
    command: str,
    cwd: str = "",
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = None,
    duration_ms: int = 0,
) -> dict[str, Any]:
    """Build a `terminal` display dict, capping stdout and stderr."""
    capped_out, trunc_out = _cap_terminal_stream(stdout)
    capped_err, trunc_err = _cap_terminal_stream(stderr)
    return {
        "kind": "terminal",
        "command": command,
        "cwd": cwd,
        "stdout": capped_out,
        "stderr": capped_err,
        "exit_code": exit_code,
        "duration_ms": max(0, duration_ms),
        "truncated": trunc_out or trunc_err,
    }


def diff(
    *,
    path: str,
    unified: str | Sequence[str],
) -> dict[str, Any]:
    """Build a `diff` display dict from `difflib.unified_diff` output."""
    raw_lines = unified.splitlines() if isinstance(unified, str) else list(unified)
    parsed: list[dict[str, str]] = []
    additions = 0
    deletions = 0
    in_hunk = False

    for raw in raw_lines:
        line = raw.rstrip("\r\n")
        if not in_hunk and (
            line.startswith("--- ")
            or line == "---"
            or line.startswith("+++ ")
            or line == "+++"
        ):
            continue
        if line.startswith("@@"):
            in_hunk = True
            continue
        if line.startswith("\\"):
            continue
        if line.startswith("+"):
            parsed.append({"kind": "added", "text": line[1:]})
            additions += 1
        elif line.startswith("-"):
            parsed.append({"kind": "removed", "text": line[1:]})
            deletions += 1
        elif line.startswith(" "):
            parsed.append({"kind": "context", "text": line[1:]})

    truncated = len(parsed) > DIFF_MAX_LINES
    return {
        "kind": "diff",
        "path": path,
        "additions": additions,
        "deletions": deletions,
        "lines": parsed[:DIFF_MAX_LINES],
        "truncated": truncated,
    }


def files(
    *,
    root: str,
    entries: Sequence[Mapping[str, Any]],
    truncated: bool = False,
) -> dict[str, Any]:
    """Build a `files` display dict capped at `FILES_MAX` entries."""
    normalised: list[dict[str, Any]] = []
    for item in entries[:FILES_MAX]:
        entry: dict[str, Any] = {
            "path": str(item["path"]),
            "kind": "folder" if item.get("kind") == "folder" else "file",
        }
        if item.get("additions") is not None:
            entry["additions"] = int(item["additions"])
        if item.get("deletions") is not None:
            entry["deletions"] = int(item["deletions"])
        normalised.append(entry)

    return {
        "kind": "files",
        "root": root,
        "entries": normalised,
        "truncated": truncated or len(entries) > FILES_MAX,
    }


def search(
    *,
    query: str,
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build a `search` display dict capped at `SEARCH_MAX` hits."""
    hits = [
        {
            "title": str(item.get("title", "")),
            "url": str(item.get("url", "")),
            "snippet": str(item.get("snippet", "")),
        }
        for item in results[:SEARCH_MAX]
    ]
    return {
        "kind": "search",
        "query": query,
        "results": hits,
        "truncated": len(results) > SEARCH_MAX,
    }


def page(
    *,
    url: str,
    title: str = "",
    site: str = "",
    description: str = "",
) -> dict[str, Any]:
    """Build a `page` link-preview display dict."""
    return {
        "kind": "page",
        "url": url,
        "title": title,
        "site": site,
        "description": description,
    }


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def table(
    *,
    headers: Sequence[Any],
    rows: Sequence[Sequence[Any]],
) -> dict[str, Any]:
    """Build a `table` display dict capped at `TABLE_MAX_ROWS` x `TABLE_MAX_COLS`."""
    raw_headers = [_cell_str(h) for h in headers]
    if not any(raw_headers) and raw_headers:
        raw_headers = [f"col{idx + 1}" for idx in range(len(raw_headers))]

    col_truncated = len(raw_headers) > TABLE_MAX_COLS
    kept_headers = raw_headers[:TABLE_MAX_COLS]

    columns: list[dict[str, str]] = []
    seen_keys: set[str] = set()
    for idx, label in enumerate(kept_headers):
        effective_label = label or f"col{idx + 1}"
        base_key = effective_label
        key = base_key
        suffix = 2
        while key in seen_keys:
            key = f"{base_key}_{suffix}"
            suffix += 1
        seen_keys.add(key)
        columns.append({"key": key, "label": effective_label})

    row_truncated = len(rows) > TABLE_MAX_ROWS
    wide_row = False
    out_rows: list[dict[str, str]] = []
    for raw_row in rows[:TABLE_MAX_ROWS]:
        if len(raw_row) > TABLE_MAX_COLS:
            wide_row = True
        row_dict: dict[str, str] = {}
        for idx, col in enumerate(columns):
            row_dict[col["key"]] = _cell_str(raw_row[idx]) if idx < len(raw_row) else ""
        out_rows.append(row_dict)

    return {
        "kind": "table",
        "columns": columns,
        "rows": out_rows,
        "truncated": col_truncated or row_truncated or wide_row,
    }


def memory(
    changes: Sequence[tuple[str, str] | Mapping[str, str]],
) -> dict[str, Any]:
    """Build a `memory` display dict from `(text, change)` pairs or dicts."""
    items: list[dict[str, str]] = []
    for entry in changes:
        if isinstance(entry, Mapping):
            items.append(
                {
                    "text": str(entry["text"]),
                    "change": str(entry["change"]),
                }
            )
        else:
            text, change = entry
            items.append({"text": str(text), "change": str(change)})
    return {
        "kind": "memory",
        "changes": items,
    }


def denied(*, rule: str, message: str) -> dict[str, Any]:
    """Build a `denied` guardrail display dict."""
    return {
        "kind": "denied",
        "rule": rule,
        "message": message,
    }


def grant(*, scope: str, reach: Sequence[str]) -> dict[str, Any]:
    """Build a `grant` permission display dict."""
    return {
        "kind": "grant",
        "scope": scope,
        "reach": [str(item) for item in reach],
    }
