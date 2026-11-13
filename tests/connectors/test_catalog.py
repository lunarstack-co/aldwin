"""The shipped connector catalogue."""

from __future__ import annotations

import statistics
import time
from importlib.resources import files
from typing import get_args

import pytest

from aldwin.core.connectors.catalog import Category, catalog_entry, load_catalog
from aldwin.core.connectors.config import check_url

ORDER = [
    "notion",
    "linear",
    "atlassian",
    "todoist",
    "monday",
    "basecamp",
    "intercom",
    "dropbox",
    "github",
    "gitlab",
    "sentry",
    "vercel",
    "digitalocean",
    "supabase",
    "postman",
    "newrelic",
    "huggingface",
    "context7",
    "stripe",
    "paypal",
    "figma",
    "canva",
    "miro",
    "webflow",
    "wix",
    "elevenlabs",
    "zapier",
    "make",
    "ifttt",
    "airtable",
    "typeform",
    "amplitude",
    "exa",
    "wolfram",
]

# Hosts anyone can publish under; a catalogue address there could be anybody's server.
SHARED_HOSTS = (
    "vercel.app",
    "netlify.app",
    "smithery.ai",
    "workers.dev",
    "github.io",
    "pages.dev",
    "onrender.com",
    "herokuapp.com",
    "fly.dev",
    "railway.app",
    "azurewebsites.net",
    "run.app",
    "pythonanywhere.com",
)


def test_entries_are_in_order_with_the_right_sign_in() -> None:
    entries = load_catalog()
    assert [e.name for e in entries] == ORDER
    auth = {e.name: e.auth for e in entries}
    assert auth["github"] == "bearer"
    assert {n for n, a in auth.items() if a == "none"} == {"context7", "exa", "wolfram"}
    assert {n for n, a in auth.items() if a == "oauth"} == set(ORDER) - {
        "github",
        "context7",
        "exa",
        "wolfram",
    }


def test_atlassian_uses_the_v2_address() -> None:
    atlassian = catalog_entry("atlassian")
    assert atlassian is not None
    assert atlassian.url == "https://mcp.atlassian.com/v2/mcp"


def test_every_entry_is_complete_and_readable() -> None:
    categories = set(get_args(Category))
    for entry in load_catalog():
        check_url(entry.url)
        assert entry.title
        assert 0 < len(entry.purpose) <= 60, entry.name
        assert not entry.purpose.endswith("."), entry.name
        assert len(entry.keywords) >= 2, entry.name
        assert entry.category in categories, entry.name
        assert entry.docs_url.startswith("https://"), entry.name


def test_no_entry_points_at_a_shared_host() -> None:
    for entry in load_catalog():
        host = entry.url.split("/")[2].split(":")[0]
        assert not any(host == h or host.endswith(f".{h}") for h in SHARED_HOSTS)


def test_the_catalogue_holds_no_client_secret() -> None:
    text = (
        files("aldwin.core.assets")
        .joinpath("connectors/catalog.yaml")
        .read_text(encoding="utf-8")
    )
    assert "client_secret" not in text


def test_github_points_at_the_token_page() -> None:
    github = catalog_entry("github")
    assert github is not None
    assert (
        github.token_help_url
        == "https://github.com/settings/personal-access-tokens/new"
    )
    assert catalog_entry("nope") is None


@pytest.mark.perf
def test_the_catalogue_loads_quickly() -> None:
    timings = []
    for _ in range(5):
        load_catalog.cache_clear()
        started = time.perf_counter()
        load_catalog()
        timings.append(time.perf_counter() - started)
    assert statistics.median(timings) < 0.02
