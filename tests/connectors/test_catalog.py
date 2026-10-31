"""The shipped connector catalogue."""

from __future__ import annotations

from aldwin.core.connectors.catalog import catalog_entry, load_catalog
from aldwin.core.connectors.config import check_url

ORDER = [
    "notion",
    "linear",
    "atlassian",
    "todoist",
    "github",
    "sentry",
    "figma",
    "dropbox",
    "context7",
]


def test_nine_entries_in_order_with_the_right_auth() -> None:
    entries = load_catalog()
    assert [e.name for e in entries] == ORDER
    auth = {e.name: e.auth for e in entries}
    assert auth["github"] == "bearer"
    assert auth["context7"] == "none"
    assert {n for n, a in auth.items() if a == "oauth"} == set(ORDER) - {
        "github",
        "context7",
    }


def test_every_entry_is_complete_and_valid() -> None:
    for entry in load_catalog():
        check_url(entry.url)
        assert entry.purpose and entry.docs_url and entry.keywords
        assert entry.title


def test_github_points_at_the_token_page() -> None:
    github = catalog_entry("github")
    assert github is not None
    assert (
        github.token_help_url
        == "https://github.com/settings/personal-access-tokens/new"
    )
    assert catalog_entry("nope") is None
