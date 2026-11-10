"""Every ``/v1`` REST route: one happy path and one signed-out request each."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from aldwin.core.providers.base import AccessStatus, ModelInfo
from aldwin.core.providers.discovery import LocalProviderStatus
from aldwin.core.settings import load_config, load_secrets
from aldwin.core.tools.web_search.hosted import KeyCheck, KeyCheckResult
from aldwin.host.sessions import SessionStore
from aldwin.web.app import create_app
from aldwin.web.auth import COOKIE_NAME, Authenticator
from aldwin.web.routes.conversations import conversations_router
from aldwin.web.routes.settings import settings_router
from aldwin.web.routes.setup import setup_router

PORT = 7841
BASE = f"http://127.0.0.1:{PORT}"
OPENAI_KEY = "sk-test-0123456789abcdef"
BRAVE_KEY = "brave-test-0123456789"

# Every HTTP route under /v1, with a body that passes validation.
HTTP_ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("GET", "/v1/health", None),
    ("GET", "/v1/home", None),
    ("GET", "/v1/conversations", None),
    ("GET", "/v1/conversations/2026-11-10_abc", None),
    ("GET", "/v1/settings", None),
    ("PATCH", "/v1/settings", {}),
    ("POST", "/v1/settings/secrets", {"kind": "provider", "name": "x", "key": "k"}),
    ("GET", "/v1/setup/status", None),
    ("POST", "/v1/setup/check-key", {"kind": "provider", "name": "x", "key": "k"}),
    ("POST", "/v1/setup/provider-key", {"name": "x", "key": "k"}),
    ("POST", "/v1/setup/detect-local", None),
    ("POST", "/v1/setup/models", {"provider": "openai"}),
    ("POST", "/v1/setup/save", {}),
    ("POST", "/v1/setup/profile", {"skipped": True}),
)
# Served by the WebSocket bridge; its signed-out check runs once it is mounted.
PENDING_ROUTES: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("POST", "/v1/ws-ticket", None),
)


@pytest.fixture
def auth() -> Authenticator:
    return Authenticator.mint("127.0.0.1", PORT)


@pytest.fixture
def anon(
    auth: Authenticator, tmp_path: Path, aldwin_home: Path
) -> Iterator[TestClient]:
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<main>Aldwin</main>", encoding="utf-8")
    app = create_app(
        authenticator=auth,
        home=aldwin_home,
        home_display="~/.aldwin",
        version="9.9.9",
        static_dir=static,
    )
    with TestClient(app, base_url=BASE) as test_client:
        yield test_client


@pytest.fixture
def api(anon: TestClient, auth: Authenticator) -> TestClient:
    assert anon.get(f"/?t={auth.token}").status_code == 200
    return anon


def _call(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None = None
) -> Any:
    return client.request(method, path, json=body)


def _store(home: Path) -> SessionStore:
    return SessionStore(home / "sessions")


def _session_with_turn(home: Path, text: str) -> str:
    store = _store(home)
    stored = store.create_new(model="openai/gpt-4o")
    stored.turns = [{"text": text, "skills": []}]
    store.save(stored, stored.memory, interrupted=False)
    return stored.meta.id


def _ok_provider(monkeypatch: pytest.MonkeyPatch, status: AccessStatus) -> list[str]:
    seen: list[str] = []

    async def fake(provider: str, key: str, **_: Any) -> AccessStatus:
        seen.append(key)
        return status

    monkeypatch.setattr("aldwin.core.providers.keys.check_provider_key", fake)
    return seen


def _ok_search(monkeypatch: pytest.MonkeyPatch, outcome: KeyCheck) -> None:
    async def fake(provider: str, key: str, **_: Any) -> KeyCheckResult:
        return KeyCheckResult(outcome, "HTTP 401" if outcome is not KeyCheck.OK else "")

    monkeypatch.setattr("aldwin.core.tools.web_search.connect.check_search_key", fake)


# Inventory and auth


@pytest.mark.parametrize(("method", "path", "body"), HTTP_ROUTES)
def test_every_route_refuses_a_signed_out_request(
    anon: TestClient, method: str, path: str, body: dict[str, Any] | None
) -> None:
    """TC-26: no cookie means 401 on every route, before any handler runs."""
    response = _call(anon, method, path, body)
    assert response.status_code == 401, path
    assert "aldwin web" in response.json()["detail"]


@pytest.mark.parametrize(("method", "path", "body"), PENDING_ROUTES)
def test_pending_routes_refuse_a_signed_out_request(
    api: TestClient,
    anon: TestClient,
    auth: Authenticator,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    """TC-08: the WebSocket ticket route is listed and checked once it exists."""
    # Before the route exists, the UI's static files answer 404 or 405.
    if _call(api, method, path, body).status_code in (404, 405):
        pytest.skip(f"{method} {path} is not served yet")
    anon.cookies.clear()
    assert _call(anon, method, path, body).status_code == 401


def test_inventory_lists_the_websocket_ticket_route() -> None:
    """TC-08: the ticket route stays in the inventory before it is served."""
    assert ("POST", "/v1/ws-ticket") in {(m, p) for m, p, _ in PENDING_ROUTES}


def test_inventory_covers_every_rest_route(tmp_path: Path) -> None:
    """A new route must join the inventory, so it gets a signed-out test."""
    served = set()
    for router in (
        conversations_router(tmp_path),
        settings_router(tmp_path),
        setup_router(tmp_path),
    ):
        for route in router.routes:
            assert isinstance(route, APIRoute)
            for method in route.methods or ():
                served.add((method, f"/v1{route.path}"))
    listed = {
        (method, "/v1/conversations/{session_id}" if "conversations/" in p else p)
        for method, p, _ in HTTP_ROUTES
        if p not in ("/v1/health", "/v1/home")
    }
    assert served == listed


def test_wrong_cookie_is_401(anon: TestClient) -> None:
    """TC-28: a cookie from another run does not sign in."""
    anon.cookies.set(COOKIE_NAME, "not-this-run")
    assert anon.get("/v1/conversations").status_code == 401


def test_foreign_origin_cannot_change_settings(api: TestClient) -> None:
    """TC-27: a write from another page is refused even with the cookie."""
    before = load_config().check_for_updates
    for headers in (
        {"Origin": "http://evil.example"},
        {"Host": "evil.example:7841"},
        {"Sec-Fetch-Site": "same-site"},
    ):
        response = api.patch(
            "/v1/settings", json={"check_for_updates": not before}, headers=headers
        )
        assert response.status_code == 403, headers
    assert load_config().check_for_updates is before


def test_no_rest_route_starts_or_replays_a_conversation(api: TestClient) -> None:
    """TC-35: starting a conversation and its history stay on the protocol."""
    assert api.post("/v1/conversations", json={}).status_code == 405
    assert api.get("/v1/conversations/x/history").status_code == 404


# Conversations


def test_list_hides_conversations_with_no_turns(
    api: TestClient, aldwin_home: Path
) -> None:
    """TC-01, TC-11: the list shows saved conversations, not empty ones."""
    kept = _session_with_turn(aldwin_home, "Plan my week")
    empty = _store(aldwin_home).create_new().meta.id

    response = api.get("/v1/conversations")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items] == [kept]
    assert items[0]["title"] == "Plan my week"
    assert set(items[0]) == {
        "id",
        "created_at",
        "updated_at",
        "model",
        "title",
        "interrupted",
    }
    assert api.get(f"/v1/conversations/{empty}").status_code == 200


def test_detail_returns_the_file_as_saved(api: TestClient, aldwin_home: Path) -> None:
    """TC-03: the detail is the session file itself, not a second schema."""
    session_id = _session_with_turn(aldwin_home, "Hello")
    on_disk = json.loads(
        (aldwin_home / "sessions" / f"{session_id}.json").read_text("utf-8")
    )

    response = api.get(f"/v1/conversations/{session_id}")

    assert response.status_code == 200
    assert response.json() == on_disk
    assert isinstance(response.json()["memory"], str)


@pytest.mark.parametrize("session_id", ["2026-01-01_missing", "..", "a.b", "%2e%2e"])
def test_missing_or_odd_id_is_404(api: TestClient, session_id: str) -> None:
    """TC-09: an unknown id, or one that is not a file name, is not found."""
    response = api.get(f"/v1/conversations/{session_id}")
    assert response.status_code == 404


def test_corrupt_conversation_is_404(api: TestClient, aldwin_home: Path) -> None:
    """TC-10: a damaged file is not found rather than a server error."""
    sessions = aldwin_home / "sessions"
    sessions.mkdir(exist_ok=True)
    (sessions / "2026-01-01_broken.json").write_text("{not json", encoding="utf-8")
    assert api.get("/v1/conversations/2026-01-01_broken").status_code == 404


# Settings


def test_settings_show_keys_masked_with_their_source(
    api: TestClient, aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-12: keys appear masked with where they came from, never in full."""
    (aldwin_home / "secrets.yaml").write_text(
        yaml.safe_dump({"openai_api_key": OPENAI_KEY}), encoding="utf-8"
    )
    os.chmod(aldwin_home / "secrets.yaml", 0o600)
    monkeypatch.setenv("BRAVE_API_KEY", BRAVE_KEY)

    response = api.get("/v1/settings")

    assert response.status_code == 200
    assert OPENAI_KEY not in response.text and BRAVE_KEY not in response.text
    body = response.json()
    assert set(body) == {"model", "check_for_updates", "limits", "search", "keys"}
    keys = {(row["kind"], row["name"]): row for row in body["keys"]}
    assert keys[("provider", "openai")]["source"] == "file"
    assert keys[("provider", "openai")]["masked"] == "sk-…cdef"
    assert keys[("search", "brave")]["source"] == "env"
    assert keys[("search", "tavily")]["source"] == "missing"
    assert keys[("search", "tavily")]["masked"] == ""
    assert body["limits"]["max_steps"] == load_config().limits.max_steps


def test_patch_limits_persists_for_the_terminal(api: TestClient) -> None:
    """TC-04: a limit saved from the browser is what the terminal loads."""
    response = api.patch("/v1/settings", json={"limits": {"context_exchanges": 12}})
    assert response.status_code == 200
    assert response.json()["limits"]["context_exchanges"] == 12
    assert load_config().limits.context_exchanges == 12


def test_patch_model(api: TestClient) -> None:
    """TC-13: the model can be changed."""
    response = api.patch("/v1/settings", json={"model": "openai/gpt-4o-mini"})
    assert response.status_code == 200
    assert load_config().model == "openai/gpt-4o-mini"


def test_patch_search(api: TestClient) -> None:
    """TC-14: the search provider and SearXNG address can be changed."""
    response = api.patch(
        "/v1/settings",
        json={"search": {"provider": "brave", "searxng_url": "http://127.0.0.1:8888"}},
    )
    assert response.status_code == 200
    search = load_config().search
    assert (search.provider, search.searxng_url) == ("brave", "http://127.0.0.1:8888")


def test_patch_check_for_updates(api: TestClient) -> None:
    """TC-15: update checks can be turned off and on."""
    assert (
        api.patch("/v1/settings", json={"check_for_updates": False}).status_code == 200
    )
    assert load_config().check_for_updates is False
    assert (
        api.patch("/v1/settings", json={"check_for_updates": True}).status_code == 200
    )
    assert load_config().check_for_updates is True


def test_empty_patch_changes_nothing(api: TestClient, aldwin_home: Path) -> None:
    """TC-16: an empty patch returns the current settings untouched."""
    config = aldwin_home / "config.yaml"
    before = config.read_text("utf-8")
    response = api.patch("/v1/settings", json={})
    assert response.status_code == 200
    assert response.json() == api.get("/v1/settings").json()
    assert config.read_text("utf-8") == before


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"openai_api_key": OPENAI_KEY}, "POST /v1/settings/secrets"),
        ({"theme": "light"}, "cannot be changed here"),
        ({"providers": {}}, "cannot be changed here"),
        ({"limits": {"nope": 1}}, "limits.nope"),
        ({"limits": {"max_steps": 0}}, "limits.max_steps"),
        ({"limits": 3}, "limits must be an object"),
        ({"search": {"provider": "duck"}}, "search.provider"),
        ({"search": {"tavily_api_key": "x"}}, "search.tavily_api_key"),
        ({"check_for_updates": "yes"}, "true or false"),
        ({"model": ""}, "model"),
        ({"model": "nowhere/model"}, "nowhere"),
    ],
)
def test_refused_patch_writes_nothing(
    api: TestClient, aldwin_home: Path, patch: dict[str, Any], message: str
) -> None:
    """TC-05: a disallowed or bad field is a 400 that names it."""
    config = aldwin_home / "config.yaml"
    before = config.read_text("utf-8")
    response = api.patch("/v1/settings", json=patch)
    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert OPENAI_KEY not in response.text
    assert config.read_text("utf-8") == before


def test_patch_mixing_a_secret_with_safe_fields_applies_none(
    api: TestClient, aldwin_home: Path
) -> None:
    """TC-17: one refused field means none of the patch is applied."""
    before = (aldwin_home / "config.yaml").read_text("utf-8")
    response = api.patch(
        "/v1/settings",
        json={"limits": {"max_steps": 30}, "openai_api_key": OPENAI_KEY},
    )
    assert response.status_code == 400
    assert (aldwin_home / "config.yaml").read_text("utf-8") == before
    assert load_secrets().openai_api_key is None


def test_patch_body_must_be_an_object(api: TestClient) -> None:
    assert api.patch("/v1/settings", json=[1]).status_code == 422


def test_secret_saved_after_check_and_not_echoed(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-06: the key is checked, then saved; the reply shows it masked only."""
    seen = _ok_provider(monkeypatch, AccessStatus.OK)

    response = api.post(
        "/v1/settings/secrets",
        json={"kind": "provider", "name": "OpenAI", "key": f" {OPENAI_KEY} "},
    )

    assert response.status_code == 200
    assert OPENAI_KEY not in response.text
    assert response.json() == {
        "ok": True,
        "kind": "provider",
        "name": "openai",
        "masked": "sk-…cdef",
        "env_override": False,
    }
    assert seen == [f" {OPENAI_KEY} "]
    secret = load_secrets().openai_api_key
    assert secret is not None and secret.get_secret_value() == OPENAI_KEY
    assert OPENAI_KEY not in api.get("/v1/settings").text


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (AccessStatus.INVALID_KEY, 400),
        (AccessStatus.NO_ACCESS, 400),
        (AccessStatus.UNREACHABLE, 502),
    ],
)
def test_rejected_secret_is_not_saved_or_echoed(
    api: TestClient, monkeypatch: pytest.MonkeyPatch, status: AccessStatus, code: int
) -> None:
    """TC-18: a key that fails its check is never written."""
    _ok_provider(monkeypatch, status)
    response = api.post(
        "/v1/settings/secrets",
        json={"kind": "provider", "name": "openai", "key": OPENAI_KEY},
    )
    assert response.status_code == code
    assert OPENAI_KEY not in response.text
    assert "OpenAI" in response.json()["detail"]
    assert load_secrets().openai_api_key is None


def test_env_key_still_wins_after_a_save(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A saved key behind an environment variable says so instead of hiding it."""
    _ok_provider(monkeypatch, AccessStatus.OK)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env-99999999")
    response = api.post(
        "/v1/settings/secrets",
        json={"kind": "provider", "name": "openai", "key": OPENAI_KEY},
    )
    assert response.status_code == 200
    assert response.json()["env_override"] is True


def test_search_secret_saved_after_check(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-19: a search key goes through the search provider's own check."""
    _ok_search(monkeypatch, KeyCheck.OK)
    response = api.post(
        "/v1/settings/secrets",
        json={"kind": "search", "name": "brave", "key": BRAVE_KEY},
    )
    assert response.status_code == 200
    assert BRAVE_KEY not in response.text
    assert load_secrets().get_secret("brave") == BRAVE_KEY
    assert load_config().search.provider == "brave"


def test_rejected_search_secret_is_not_saved(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ok_search(monkeypatch, KeyCheck.REJECTED)
    response = api.post(
        "/v1/settings/secrets",
        json={"kind": "search", "name": "brave", "key": BRAVE_KEY},
    )
    assert response.status_code == 400
    assert "Brave" in response.json()["detail"]
    assert load_secrets().get_secret("brave") is None


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "provider", "name": "nowhere", "key": OPENAI_KEY},
        {"kind": "provider", "name": "ollama", "key": OPENAI_KEY},
        {"kind": "search", "name": "searxng", "key": OPENAI_KEY},
    ],
)
def test_unknown_or_keyless_provider_is_400(
    api: TestClient, body: dict[str, Any]
) -> None:
    response = api.post("/v1/settings/secrets", json=body)
    assert response.status_code == 400
    assert response.json()["status"] == "unknown"
    assert OPENAI_KEY not in response.text


def test_invalid_body_never_echoes_the_key(api: TestClient) -> None:
    """A malformed request is refused without sending the pasted key back."""
    for body in (
        {"name": "openai", "key": OPENAI_KEY},
        {"kind": "provider", "name": "openai", "key": OPENAI_KEY, "extra": 1},
        {"kind": "email", "name": "openai", "key": OPENAI_KEY},
    ):
        response = api.post("/v1/settings/secrets", json=body)
        assert response.status_code == 422
        assert OPENAI_KEY not in response.text
        assert response.json()["detail"][0]["loc"]


# Setup


def test_setup_status_on_a_fresh_home(api: TestClient) -> None:
    """TC-20: a new home has no keys and no profile yet."""
    response = api.get("/v1/setup/status")
    assert response.status_code == 200
    body = response.json()
    assert body["has_openai_key"] is False
    assert body["has_provider_key"] is False
    assert body["profile_done"] is False
    assert body["profile_skipped"] is False


def test_check_key_never_saves(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-21: checking a key answers ok without writing it."""
    _ok_provider(monkeypatch, AccessStatus.OK)
    response = api.post(
        "/v1/setup/check-key",
        json={"kind": "provider", "name": "openai", "key": OPENAI_KEY},
    )
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "status": "ok",
        "message": "OpenAI accepted the key.",
    }
    assert load_secrets().openai_api_key is None


def test_check_key_reports_a_rejection(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ok_search(monkeypatch, KeyCheck.REJECTED)
    response = api.post(
        "/v1/setup/check-key",
        json={"kind": "search", "name": "brave", "key": BRAVE_KEY},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["status"] == "rejected"
    assert BRAVE_KEY not in response.text


def test_provider_key_saves_and_opens_the_keys_gate(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-07: a working key saved during setup makes the keys step pass."""
    _ok_provider(monkeypatch, AccessStatus.OK)
    response = api.post(
        "/v1/setup/provider-key", json={"name": "openai", "key": OPENAI_KEY}
    )
    assert response.status_code == 200
    assert response.json()["masked"] == "sk-…cdef"
    assert OPENAI_KEY not in response.text
    status = api.get("/v1/setup/status").json()
    assert status["has_provider_key"] is True
    assert status["masked_openai_key"] == "sk-…cdef"


def test_detect_local_lists_runtimes(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-22: local runtimes come back with their models."""
    model = ModelInfo(
        id="llama3",
        title="Llama 3",
        context_window=8192,
        supports_tools=True,
        local=True,
    )

    async def fake(**_: Any) -> list[LocalProviderStatus]:
        return [
            LocalProviderStatus(
                name="ollama",
                title="Ollama",
                base_url="http://127.0.0.1:11434",
                running=True,
                models=(model,),
            )
        ]

    monkeypatch.setattr("aldwin.client.detect_local_providers", fake)
    response = api.post("/v1/setup/detect-local")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["name"] == "ollama" and item["running"] is True
    assert item["models"][0]["id"] == "llama3"


def test_models_lists_a_providers_models(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-23: the model picker gets the provider's models."""
    asked: list[str] = []

    async def fake(provider: str, home: Path | None = None) -> list[ModelInfo]:
        asked.append(provider)
        return [ModelInfo("gpt-4o", "GPT-4o", 128_000, True, False)]

    monkeypatch.setattr("aldwin.client.list_models_for", fake)
    response = api.post("/v1/setup/models", json={"provider": " OpenAI "})
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "gpt-4o",
                "title": "GPT-4o",
                "context_window": 128_000,
                "supports_tools": True,
                "local": False,
            }
        ]
    }
    assert asked == ["openai"]


def test_setup_save_checks_keys_then_saves_keys_and_model(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-24: the terminal's first-run answers can be saved from the browser."""
    _ok_provider(monkeypatch, AccessStatus.OK)
    _ok_search(monkeypatch, KeyCheck.OK)
    response = api.post(
        "/v1/setup/save",
        json={
            "openai_api_key": OPENAI_KEY,
            "tavily_api_key": "tvly-test-0123456789",
            "model": "openai/gpt-4o-mini",
        },
    )
    assert response.status_code == 200
    assert OPENAI_KEY not in response.text
    assert response.json()["has_tavily_key"] is True
    secrets = load_secrets()
    assert secrets.get_secret("openai") == OPENAI_KEY
    assert secrets.get_secret("tavily") == "tvly-test-0123456789"
    assert load_config().model == "openai/gpt-4o-mini"


def test_setup_save_refuses_theme(api: TestClient, aldwin_home: Path) -> None:
    """TC-24: the browser's appearance is not a terminal setting."""
    before = (aldwin_home / "config.yaml").read_text("utf-8")
    response = api.post("/v1/setup/save", json={"theme": "light"})
    assert response.status_code == 422
    assert (aldwin_home / "config.yaml").read_text("utf-8") == before


def test_setup_save_with_a_bad_key_saves_nothing(
    api: TestClient, aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ok_provider(monkeypatch, AccessStatus.OK)
    _ok_search(monkeypatch, KeyCheck.REJECTED)
    before = (aldwin_home / "config.yaml").read_text("utf-8")
    response = api.post(
        "/v1/setup/save",
        json={
            "openai_api_key": OPENAI_KEY,
            "tavily_api_key": "tvly-bad-0123456789",
            "model": "openai/gpt-4o-mini",
        },
    )
    assert response.status_code == 400
    assert "Tavily" in response.json()["detail"]
    assert load_secrets().openai_api_key is None
    assert (aldwin_home / "config.yaml").read_text("utf-8") == before


def test_setup_save_failing_key_write_leaves_the_model(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keys are written before the model, so a failed write changes neither."""
    _ok_provider(monkeypatch, AccessStatus.OK)

    def broken(**_: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("aldwin.client.save_setup", broken)
    before = load_config().model
    with pytest.raises(OSError):
        api.post(
            "/v1/setup/save",
            json={"openai_api_key": OPENAI_KEY, "model": "openai/gpt-4o-mini"},
        )
    assert load_config().model == before


def test_profile_is_saved_and_marks_the_step_done(
    api: TestClient, aldwin_home: Path
) -> None:
    """TC-25: the profile step writes state/onboarding.json, mode 0600."""
    response = api.post(
        "/v1/setup/profile",
        json={"first_name": " Ada ", "occupation": "Engineer", "personality": None},
    )
    assert response.status_code == 200
    path = aldwin_home / "state" / "onboarding.json"
    saved = json.loads(path.read_text("utf-8"))
    assert saved["first_name"] == "Ada" and saved["occupation"] == "Engineer"
    assert "personality" not in saved and saved["skipped"] is False
    assert (path.stat().st_mode & 0o777) == 0o600
    status = api.get("/v1/setup/status").json()
    assert status["profile_done"] is True and status["profile_skipped"] is False


def test_profile_can_be_skipped(api: TestClient, aldwin_home: Path) -> None:
    response = api.post(
        "/v1/setup/profile", json={"first_name": "Ada", "skipped": True}
    )
    assert response.status_code == 200
    saved = json.loads((aldwin_home / "state" / "onboarding.json").read_text("utf-8"))
    assert "first_name" not in saved
    assert api.get("/v1/setup/status").json()["profile_skipped"] is True


def test_profile_field_too_long_is_400(api: TestClient, aldwin_home: Path) -> None:
    response = api.post("/v1/setup/profile", json={"first_name": "A" * 201})
    assert response.status_code == 400
    assert "first_name" in response.json()["detail"]
    assert not (aldwin_home / "state" / "onboarding.json").exists()


def test_broken_config_is_a_400_with_the_fix(
    api: TestClient, aldwin_home: Path
) -> None:
    """A config file that does not load is reported, not a server error."""
    (aldwin_home / "config.yaml").write_text("limits: {max_steps: 0}\n", "utf-8")
    for path in ("/v1/settings", "/v1/setup/status"):
        response = api.get(path)
        assert response.status_code == 400, path
        assert "max_steps" in response.json()["detail"]


# Docs

DOCS = Path(__file__).resolve().parent.parent / "docs"


def test_protocol_doc_lists_every_rest_route() -> None:
    """TC-37: each route is documented next to the protocol it does not replace."""
    doc = (DOCS / "protocol.md").read_text(encoding="utf-8")
    section = doc.split("## REST helpers in `aldwin web`", 1)[1].split("\n## ", 1)[0]
    for method, path, _ in HTTP_ROUTES:
        documented = "/v1/conversations/{id}" if "conversations/" in path else path
        assert f"{method} {documented}" in section, (method, path)
    assert "not a second protocol" in section


def test_privacy_doc_covers_onboarding_and_masked_keys() -> None:
    """TC-36: the privacy page says what the profile step stores and how keys show."""
    doc = (DOCS / "privacy.md").read_text(encoding="utf-8")
    assert "onboarding.json" in doc
    assert "no answer from the server ever contains one" in doc
