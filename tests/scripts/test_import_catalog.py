"""The maintainer script that builds the connector catalogue from the MCP Registry."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx2
import pytest
import yaml
from pydantic import ValidationError

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/connectors/import_catalog.py"
SHIPPED_CATALOG = REPO / "src/aldwin/core/assets/connectors/catalog.yaml"
SHIPPED_MARKS = REPO / "web/src/components/brand/logos/marks.ts"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("import_catalog", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look their module up in sys.modules while the module runs.
    sys.modules["import_catalog"] = module
    spec.loader.exec_module(module)
    return module


ic = _load_script()


def vendor(**fields: Any) -> Any:
    base: dict[str, Any] = {
        "name": "stripe",
        "title": "Stripe",
        "purpose": "Payments",
        "keywords": ["stripe", "payments"],
        "category": "payments",
        "docs_url": "https://docs.stripe.com/mcp",
        "logo": "simple-icons:stripe",
        "source": "registry",
        "namespace": "com.stripe",
    }
    base.update(fields)
    return ic.Vendor.model_validate(base)


def entry(
    name: str,
    urls: list[str],
    *,
    status: str = "active",
    latest: bool = True,
    kind: str = "streamable-http",
) -> dict[str, Any]:
    return {
        "server": {
            "name": name,
            "remotes": [{"type": kind, "url": url} for url in urls],
        },
        "_meta": {
            "io.modelcontextprotocol.registry/official": {
                "status": status,
                "isLatest": latest,
            }
        },
    }


SAMPLE = [
    entry("app.linear/linear", ["https://mcp.linear.app/mcp"]),
    entry("co.civai.nova/mcp-linear", ["https://nova.civai.co/mcp/agents/mcp-linear"]),
    entry("com.stripe/mcp", ["https://mcp.stripe.com"]),
]


# --- allowlist -----------------------------------------------------------------------


def test_the_shipped_allowlist_loads_in_order_with_unique_names() -> None:
    vendors = ic.load_allowlist()
    names = [v.name for v in vendors]
    shipped = yaml.safe_load(SHIPPED_CATALOG.read_text(encoding="utf-8"))["entries"]
    assert names == [e["name"] for e in shipped]
    assert len(set(names)) == len(names)


def test_an_unknown_allowlist_key_is_refused() -> None:
    with pytest.raises(ValidationError, match="colour"):
        vendor(colour="red")


def test_a_logo_from_an_unknown_source_is_refused() -> None:
    with pytest.raises(ValidationError, match="logo"):
        vendor(logo="icons8:stripe")


def test_source_fields_must_match_the_source() -> None:
    with pytest.raises(ValidationError, match="namespace"):
        vendor(namespace=None)
    with pytest.raises(ValidationError, match="url"):
        vendor(source="manual", namespace=None)


def test_a_token_app_needs_a_token_help_link() -> None:
    with pytest.raises(ValidationError, match="token_help_url"):
        vendor(expect_auth="bearer")


def test_repeated_names_in_the_allowlist_are_refused(tmp_path: Path) -> None:
    item = vendor().model_dump(exclude_none=True)
    path = tmp_path / "allowlist.yaml"
    path.write_text(yaml.safe_dump({"vendors": [item, item]}), encoding="utf-8")
    with pytest.raises(ValueError, match="repeat: stripe"):
        ic.load_allowlist(path)


# --- registry ------------------------------------------------------------------------


def test_the_vendors_own_entry_is_chosen() -> None:
    linear = vendor(name="linear", title="Linear", namespace="app.linear")
    assert ic.select_remote(SAMPLE, linear) == "https://mcp.linear.app/mcp"


def test_a_look_alike_under_another_namespace_is_ignored() -> None:
    linear = vendor(name="linear", title="Linear", namespace="app.linear")
    assert ic.select_remote(SAMPLE[1:2], linear) == ic.Rejection(
        "no current entry in the registry"
    )


def test_an_address_with_placeholders_is_refused() -> None:
    servers = [entry("com.microsoft/x", ["https://x.microsoft.com/{tenant_id}/mcp"])]
    result = ic.select_remote(servers, vendor(namespace="com.microsoft"))
    assert result == ic.Rejection("address needs details")


@pytest.mark.parametrize(
    "namespace", ["app.vercel.foo", "ai.smithery", "io.github.someone"]
)
def test_a_shared_host_namespace_is_refused(namespace: str) -> None:
    servers = [entry(f"{namespace}/x", ["https://foo.vercel.app/mcp"])]
    result = ic.select_remote(servers, vendor(namespace=namespace))
    assert result == ic.Rejection("namespace is a shared host")


def test_a_server_off_the_vendors_domain_is_refused() -> None:
    servers = [entry("com.stripe/mcp", ["https://stripe-mcp.example.net/mcp"])]
    result = ic.select_remote(servers, vendor())
    assert result == ic.Rejection("server isn't on the vendor's domain")


@pytest.mark.parametrize(
    ("status", "latest"), [("deprecated", True), ("active", False)]
)
def test_an_inactive_or_older_entry_is_refused(status: str, latest: bool) -> None:
    servers = [
        entry(
            "com.stripe/mcp", ["https://mcp.stripe.com"], status=status, latest=latest
        )
    ]
    result = ic.select_remote(servers, vendor())
    assert result == ic.Rejection("no current entry in the registry")


def test_an_sse_only_entry_is_refused() -> None:
    servers = [entry("com.stripe/mcp", ["https://mcp.stripe.com/sse"], kind="sse")]
    assert ic.select_remote(servers, vendor()) == ic.Rejection(
        "no streamable HTTP address"
    )


def test_the_first_remote_is_used_unless_one_is_named() -> None:
    servers = [
        entry(
            "app.linear/linear",
            ["https://mcp.linear.app/mcp", "https://mcp.linear.app/mcp/readonly"],
        )
    ]
    linear = vendor(name="linear", title="Linear", namespace="app.linear")
    assert ic.select_remote(servers, linear) == "https://mcp.linear.app/mcp"
    named = vendor(
        name="linear",
        title="Linear",
        namespace="app.linear",
        remote="https://mcp.linear.app/mcp/readonly",
    )
    assert ic.select_remote(servers, named) == "https://mcp.linear.app/mcp/readonly"


def _client(handler: Any) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


async def test_the_registry_is_paged_once_then_read_from_the_cache(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        cursor = request.url.params.get("cursor")
        calls.append(cursor or "")
        pages = {
            "": ("p2", [SAMPLE[0]]),
            "p2": ("p3", [SAMPLE[1]]),
            "p3": (None, [SAMPLE[2]]),
        }
        next_cursor, servers = pages[cursor or ""]
        metadata = {"nextCursor": next_cursor} if next_cursor else {}
        return httpx2.Response(200, json={"servers": servers, "metadata": metadata})

    cache = tmp_path / "registry.json"
    async with _client(handler) as http:
        first = await ic.fetch_registry(http, cache=cache, refresh=True)
        second = await ic.fetch_registry(http, cache=cache)
    assert first == SAMPLE == second
    assert calls == ["", "p2", "p3"]


async def test_one_retry_after_a_429(tmp_path: Path) -> None:
    answers = [httpx2.Response(429, headers={"retry-after": "0"})]

    def handler(request: httpx2.Request) -> httpx2.Response:
        if answers:
            return answers.pop()
        return httpx2.Response(200, json={"servers": SAMPLE, "metadata": {}})

    cache = tmp_path / "registry.json"
    async with _client(handler) as http:
        assert await ic.fetch_registry(http, cache=cache, refresh=True) == SAMPLE
    assert json.loads(cache.read_text(encoding="utf-8")) == SAMPLE


async def test_an_outage_keeps_the_old_cache_and_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "registry.json"
    cache.write_text("[]", encoding="utf-8")

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503)

    async with _client(handler) as http:
        with pytest.raises(ic.RegistryUnavailable, match="HTTP 503"):
            await ic.fetch_registry(http, cache=cache, refresh=True)
    assert cache.read_text(encoding="utf-8") == "[]"

    async def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise ic.RegistryUnavailable("HTTP 503")

    monkeypatch.setattr(ic, "build", unavailable)
    assert await ic._main([]) == 2
    assert "Couldn't reach the MCP Registry: HTTP 503" in capsys.readouterr().err


# --- sign-in -------------------------------------------------------------------------


@pytest.mark.loopback
@pytest.mark.parametrize(("dcr", "cimd"), [(True, False), (False, True)])
async def test_browser_sign_in_with_dynamic_registration_or_a_metadata_document(
    oauth_server: Any, dcr: bool, cimd: bool
) -> None:
    oauth_server.config.dcr = dcr
    oauth_server.config.cimd = cimd
    async with httpx2.AsyncClient() as http:
        assert await ic.classify_auth(vendor(), oauth_server.mcp_url, http) == "oauth"


@pytest.mark.loopback
async def test_browser_sign_in_with_no_way_to_register_is_refused(
    oauth_server: Any,
) -> None:
    oauth_server.config.dcr = False
    oauth_server.config.cimd = False
    async with httpx2.AsyncClient() as http:
        result = await ic.classify_auth(vendor(), oauth_server.mcp_url, http)
        assert result == ic.Rejection("Aldwin can't register with this app")
        with_client = vendor(oauth={"client_id": "aldwin-client"})
        assert (
            await ic.classify_auth(with_client, oauth_server.mcp_url, http) == "oauth"
        )


@pytest.mark.loopback
async def test_a_sign_in_server_without_pkce_is_refused(oauth_server: Any) -> None:
    oauth_server.config.s256 = False
    async with httpx2.AsyncClient() as http:
        result = await ic.classify_auth(vendor(), oauth_server.mcp_url, http)
    assert result == ic.Rejection("sign-in server doesn't support PKCE S256")


@pytest.mark.loopback
async def test_a_bare_401_is_token_sign_in_and_needs_a_help_link(
    fixture_url: str,
) -> None:
    help_link = "https://dashboard.stripe.com/apikeys"
    async with httpx2.AsyncClient() as http:
        with_help = vendor(token_help_url=help_link)
        assert await ic.classify_auth(with_help, f"{fixture_url}/401", http) == "bearer"
        result = await ic.classify_auth(vendor(), f"{fixture_url}/401", http)
    assert result == ic.Rejection(
        "token sign-in needs a token_help_url in the allowlist"
    )


@pytest.mark.loopback
async def test_an_open_server_needs_no_sign_in(fixture_url: str) -> None:
    async with httpx2.AsyncClient() as http:
        assert await ic.classify_auth(vendor(), f"{fixture_url}/mcp", http) == "none"


async def test_an_unexpected_status_or_timeout_is_refused() -> None:
    def not_found(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(404)

    def timed_out(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    async with _client(not_found) as http:
        result = await ic.classify_auth(vendor(), "https://mcp.stripe.com", http)
    assert result == ic.Rejection("server didn't answer (HTTP 404)")
    async with _client(timed_out) as http:
        result = await ic.classify_auth(vendor(), "https://mcp.stripe.com", http)
    assert isinstance(result, ic.Rejection)
    assert result.reason.startswith("server didn't answer (")


@pytest.mark.loopback
async def test_an_expected_sign_in_must_match(oauth_server: Any) -> None:
    async with httpx2.AsyncClient() as http:
        open_expected = vendor(expect_auth="none")
        result = await ic.classify_auth(open_expected, oauth_server.mcp_url, http)
        assert result == ic.Rejection("expected no sign-in, found browser sign-in")
        oauth_server.config.dcr = False
        token_app = vendor(expect_auth="bearer", token_help_url="https://example.com/t")
        assert await ic.classify_auth(token_app, oauth_server.mcp_url, http) == "bearer"


# --- writer --------------------------------------------------------------------------


def _offline_paths(
    tmp_path: Path, allowlist: list[Any], old: str | None
) -> dict[str, Path]:
    paths = {
        "allowlist": tmp_path / "allowlist.yaml",
        "catalog_path": tmp_path / "catalog.yaml",
        "marks_path": tmp_path / "app-marks.ts",
        "hand_marks": SHIPPED_MARKS,
        "cache_dir": tmp_path / "cache",
    }
    items = [v.model_dump(exclude_none=True) for v in allowlist]
    paths["allowlist"].write_text(yaml.safe_dump({"vendors": items}), encoding="utf-8")
    if old is not None:
        paths["catalog_path"].write_text(old, encoding="utf-8")
    servers = [
        entry(
            f"{v.namespace}/mcp",
            [v.remote or f"https://mcp.{ic.namespace_domain(v.namespace)}/mcp"],
        )
        for v in allowlist
        if v.source == "registry"
    ]
    paths["cache_dir"].mkdir()
    (paths["cache_dir"] / "registry.json").write_text(
        json.dumps(servers), encoding="utf-8"
    )
    return paths


async def _run(paths: dict[str, Path], **options: Any) -> tuple[int, Any]:
    async with httpx2.AsyncClient() as http:
        return await ic.build(ic.Options(offline=True, **options), http, **paths)


async def test_the_shipped_catalogue_rebuilds_byte_for_byte_and_again_with_no_changes(
    tmp_path: Path,
) -> None:
    shipped = SHIPPED_CATALOG.read_text(encoding="utf-8")
    vendors = ic.load_allowlist()
    pinned = [
        v.model_copy(
            update={"remote": ic.existing_entries(SHIPPED_CATALOG)[v.name]["url"]}
        )
        if v.source == "registry"
        else v
        for v in vendors
    ]
    paths = _offline_paths(tmp_path, pinned, shipped)
    code, report = await _run(paths)
    assert code == 0
    assert paths["catalog_path"].read_text(encoding="utf-8") == shipped
    assert report.render() == "No changes."
    code, report = await _run(paths)
    assert (code, report.render()) == (0, "No changes.")


async def test_a_left_out_vendor_is_reported_and_exits_1(tmp_path: Path) -> None:
    paths = _offline_paths(tmp_path, [vendor()], old=None)
    code, report = await _run(paths)
    assert code == 1
    assert (
        report.render()
        == "Left out: stripe (--offline can't check sign-in for a new app)"
    )
    assert "stripe" not in paths["catalog_path"].read_text(encoding="utf-8")


async def test_a_vendor_missing_from_the_cache_suggests_refresh(
    tmp_path: Path,
) -> None:
    paths = _offline_paths(tmp_path, [vendor(expect_auth="oauth")], old=None)
    (paths["cache_dir"] / "registry.json").write_text("[]", encoding="utf-8")
    code, report = await _run(paths)
    assert code == 1
    assert report.render() == (
        "Left out: stripe (no current entry in the registry; "
        "the cached registry may be old, run with --refresh)"
    )


def test_a_hand_logo_missing_from_marks_ts_is_named() -> None:
    report = ic.Report(missing_hand=["acme"])
    assert report.render() == "No hand-drawn mark in marks.ts: acme"


async def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    paths = _offline_paths(tmp_path, [vendor(expect_auth="oauth")], old=None)
    code, report = await _run(paths, dry_run=True)
    assert (code, report.render()) == (0, "Added: stripe")
    assert not paths["catalog_path"].exists()


async def test_only_rebuilds_one_app_and_keeps_the_rest(tmp_path: Path) -> None:
    linear = vendor(
        name="linear", title="Linear", namespace="app.linear", expect_auth="oauth"
    )
    stripe = vendor(expect_auth="oauth")
    old = ic.render_catalog([ic.Built(linear, "https://old.linear.app/mcp", "oauth")])
    paths = _offline_paths(tmp_path, [linear, stripe], old)
    code, report = await _run(paths, only="stripe")
    assert code == 0
    assert report.render() == "Added: stripe"
    written = ic.existing_entries(paths["catalog_path"])
    assert written["linear"]["url"] == "https://old.linear.app/mcp"
    assert written["stripe"]["url"] == "https://mcp.stripe.com/mcp"


async def test_the_report_names_added_and_changed_apps(tmp_path: Path) -> None:
    atlassian = vendor(
        name="atlassian",
        title="Atlassian",
        namespace="com.atlassian",
        remote="https://mcp.atlassian.com/v2/mcp",
        expect_auth="oauth",
    )
    stripe = vendor(expect_auth="oauth")
    old = ic.render_catalog(
        [ic.Built(atlassian, "https://mcp.atlassian.com/v1/mcp/authv2", "oauth")]
    )
    paths = _offline_paths(tmp_path, [atlassian, stripe], old)
    code, report = await _run(paths)
    assert code == 0
    assert report.render() == (
        "Added: stripe\nChanged: atlassian (url → https://mcp.atlassian.com/v2/mcp)"
    )


# --- logos ---------------------------------------------------------------------------

STRIPE_SVG = (
    '<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">'
    '<title>Stripe</title><path d="M13.976 9.15c-2.172-.806"/></svg>'
)
LOBEHUB_SVG = (
    '<svg fill="currentColor" fill-rule="evenodd" height="1em" style="flex:none" '
    'viewBox="0 0 24 24" width="1em" xmlns="http://www.w3.org/2000/svg">'
    '<title>Exa</title><path d="M3 0h19v1.791L13.892 12"/>'
    '<path d="M3 21h5" opacity=".5"/></svg>'
)


def test_a_simple_icons_svg_becomes_one_path_in_the_brand_colour() -> None:
    mark = ic.parse_svg(STRIPE_SVG, title="Stripe", color="#635BFF")
    assert mark == ic.MarkData(
        title="Stripe",
        view_box="0 0 24 24",
        paths=[{"d": "M13.976 9.15c-2.172-.806"}],
        color="#635BFF",
    )


def test_a_lobehub_svg_keeps_every_path_in_order() -> None:
    mark = ic.parse_svg(LOBEHUB_SVG, title="Exa", color=None)
    assert mark is not None
    assert mark.paths == [
        {"d": "M3 0h19v1.791L13.892 12"},
        {"d": "M3 21h5", "opacity": 0.5},
    ]
    assert mark.fill_rule == "evenodd"


@pytest.mark.parametrize(
    "svg",
    [
        '<svg viewBox="0 0 24 24"><script>alert(1)</script><path d="M0 0"/></svg>',
        '<svg viewBox="0 0 24 24"><image href="https://x.test/a.png"/></svg>',
        '<svg viewBox="0 0 24 24" xmlns:xlink="http://www.w3.org/1999/xlink">'
        '<use xlink:href="#a"/></svg>',
        '<svg viewBox="0 0 24 24" onload="alert(1)"><path d="M0 0"/></svg>',
        '<svg viewBox="0 0 24 24"><path d="M0 0" opacity="half"/></svg>',
        "<svg><path d='M0 0'/></svg>",
        "not svg",
    ],
)
def test_anything_beyond_plain_paths_becomes_a_letter(svg: str) -> None:
    assert ic.parse_svg(svg, title="X", color=None) is None


async def test_logos_come_from_the_pinned_versions_only() -> None:
    requested: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return httpx2.Response(200, text=STRIPE_SVG)

    async with _client(handler) as http:
        await ic.fetch_mark(vendor(), http, {"stripe": "#635BFF"})
        await ic.fetch_mark(
            vendor(name="exa", title="Exa", logo="lobehub:exa"), http, {}
        )
        assert await ic.fetch_mark(vendor(logo="letter"), http, {}) is None
        assert await ic.fetch_mark(vendor(logo="hand"), http, {}) is None
    assert requested == [
        "https://cdn.jsdelivr.net/npm/simple-icons@16.37.0/icons/stripe.svg",
        "https://cdn.jsdelivr.net/npm/@lobehub/icons-static-svg@1.102.0/icons/exa.svg",
    ]


@pytest.mark.parametrize(
    ("hex_color", "expected"),
    [("#635BFF", "#635BFF"), ("#000000", None), ("#FFFFFF", None), ("#050038", None)],
)
def test_near_black_and_near_white_marks_follow_the_text_colour(
    hex_color: str, expected: str | None
) -> None:
    assert ic.usable_color(hex_color) == expected


def test_the_generated_marks_file_is_plain_data() -> None:
    mark = ic.parse_svg(STRIPE_SVG, title="Stripe", color="#635BFF")
    text = ic.render_app_marks({"stripe": mark})
    assert text.startswith("// Generated by scripts/connectors/import_catalog.py")
    assert "export const APP_MARKS: Record<string, Mark> = {" in text
    body = text.split("= {", 1)[1]
    assert "<" not in body and "href" not in body and "script" not in body


def test_simple_icons_slugs_follow_its_rules() -> None:
    assert ic.simple_icons_slug("monday.com") == "mondaydotcom"
    assert ic.simple_icons_slug("C++") == "cplusplus"
    assert ic.simple_icons_slug("Hugging Face") == "huggingface"


def test_hand_kept_marks_are_read_from_marks_ts() -> None:
    hand = ic.hand_kept_marks()
    assert {"notion", "linear", "github", "exa"} <= hand


def test_every_hand_logo_is_drawn_in_marks_ts() -> None:
    drawn = ic.hand_kept_marks()
    hand = {v.name for v in ic.load_allowlist() if v.logo == "hand"}
    assert hand == {"monday", "context7", "canva", "amplitude", "exa"}
    assert hand <= drawn
