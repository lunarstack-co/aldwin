"""First-run gates in ``aldwin web``: keys start the host, the profile never blocks."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from aldwin.core.memory import read_profile
from aldwin.core.persona import read_default_persona, read_persona
from aldwin.core.providers.base import AccessStatus, ModelInfo
from aldwin.core.settings import load_config, load_secrets
from aldwin.host.home_api import (
    SettingsError,
    get_setup_status,
    keys_gate_ok,
    save_onboarding_profile,
)
from aldwin.host.in_process import get_onboarding_status
from aldwin.web.app import create_app
from aldwin.web.auth import Authenticator
from aldwin.web.runtime import WebHostRuntime

PORT = 7841
BASE = f"http://127.0.0.1:{PORT}"
OPENAI_KEY = "sk-test-0123456789abcdef"


def _write_openai_key(home: Path) -> None:
    path = home / "secrets.yaml"
    path.write_text(yaml.safe_dump({"openai_api_key": OPENAI_KEY}), encoding="utf-8")
    path.chmod(0o600)


def _check_returns(monkeypatch: pytest.MonkeyPatch, status: AccessStatus) -> None:
    async def fake(provider: str, key: str, **_: Any) -> AccessStatus:
        return status

    monkeypatch.setattr("aldwin.core.providers.keys.check_provider_key", fake)


@contextmanager
def _web(home: Path, tmp_path: Path) -> Iterator[tuple[TestClient, WebHostRuntime]]:
    """A signed-in browser against ``aldwin web`` on ``home``, lifespan included."""
    static = tmp_path / "static"
    static.mkdir(exist_ok=True)
    (static / "index.html").write_text("<main>Aldwin</main>", encoding="utf-8")
    runtime = WebHostRuntime(home=home)
    auth = Authenticator.mint("127.0.0.1", PORT)
    app = create_app(
        authenticator=auth,
        home=home,
        home_display="~",
        version="1",
        static_dir=static,
        runtime=runtime,
    )
    with TestClient(app, base_url=BASE) as client:
        assert client.get(f"/?t={auth.token}").status_code == 200
        yield client, runtime


def _status(client: TestClient) -> dict[str, Any]:
    response = client.get("/v1/setup/status")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def _ticket_status(client: TestClient) -> int:
    return client.post("/v1/ws-ticket", headers={"Origin": BASE}).status_code


# The keys gate


def test_fresh_home_has_no_working_keys(aldwin_home: Path) -> None:
    """TC-06."""
    assert keys_gate_ok(aldwin_home) is False


def test_an_openai_key_in_the_file_passes(aldwin_home: Path) -> None:
    """TC-07."""
    _write_openai_key(aldwin_home)
    assert keys_gate_ok(aldwin_home) is True


def test_a_local_model_passes_without_any_key(aldwin_home: Path) -> None:
    """TC-08: a model on this computer needs no cloud key."""
    from aldwin.core.settings import update_config_model

    update_config_model("ollama/llama3", aldwin_home)
    assert keys_gate_ok(aldwin_home) is True


def test_an_environment_key_passes_and_is_never_shown(
    aldwin_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-09, TC-25."""
    monkeypatch.setenv("OPENAI_API_KEY", OPENAI_KEY)
    assert keys_gate_ok(aldwin_home) is True
    status = get_setup_status(aldwin_home)
    assert status["keys_ok"] is True
    assert OPENAI_KEY not in json.dumps(status)


# The profile step


def test_answers_are_kept_as_drafts_until_finished(aldwin_home: Path) -> None:
    """TC-13: each Continue saves its answer; the step is done only at Finish."""
    save_onboarding_profile({"first_name": "Ada"}, home=aldwin_home)
    save_onboarding_profile({"occupation": "Engineer"}, home=aldwin_home)

    status = get_setup_status(aldwin_home)
    assert status["profile"]["first_name"] == "Ada"
    assert status["profile"]["occupation"] == "Engineer"
    assert status["profile_done"] is False
    assert read_profile(aldwin_home)[0] == ""


def test_finishing_seeds_the_default_profile_and_persona(aldwin_home: Path) -> None:
    """TC-11, TC-34: answers reach profile.md and persona.md while they are stock."""
    save_onboarding_profile(
        {"first_name": "Ada", "occupation": "Engineer"}, home=aldwin_home
    )
    save_onboarding_profile(
        {"personality": ["concise", "teaching", "concise"]},
        complete=True,
        home=aldwin_home,
    )

    assert get_setup_status(aldwin_home)["profile_done"] is True
    profile = (aldwin_home / "memory" / "profile.md").read_text("utf-8")
    assert "<!-- Profile:" in profile
    assert "- First name: Ada" in profile and "- Occupation: Engineer" in profile
    persona = read_persona(aldwin_home)
    assert persona.startswith(read_default_persona())
    assert "Keep answers short" in persona and "teach as you go" in persona
    assert persona.count("Keep answers short") == 1


def test_finishing_never_overwrites_what_the_user_wrote(aldwin_home: Path) -> None:
    """TC-12: custom profile and persona text are left alone."""
    profile_path = aldwin_home / "memory" / "profile.md"
    profile_path.write_text("I live in Lagos.\n", encoding="utf-8")
    (aldwin_home / "persona.md").write_text("Speak like a pirate.\n", "utf-8")

    save_onboarding_profile(
        {"first_name": "Ada", "personality": ["friendly"]},
        complete=True,
        home=aldwin_home,
    )

    assert profile_path.read_text("utf-8") == "I live in Lagos.\n"
    assert read_persona(aldwin_home) == "Speak like a pirate."


def test_skipping_clears_drafts_and_seeds_nothing(aldwin_home: Path) -> None:
    """TC-10, TC-35: Skip marks the step done and keeps nothing typed."""
    save_onboarding_profile({"first_name": "Ada"}, home=aldwin_home)
    save_onboarding_profile({}, skipped=True, home=aldwin_home)

    status = get_setup_status(aldwin_home)
    assert status["profile_done"] is True and status["profile_skipped"] is True
    assert status["profile"]["first_name"] == ""
    assert read_profile(aldwin_home)[0] == ""
    assert read_persona(aldwin_home) == read_default_persona()


def test_typed_answers_stay_on_one_line(aldwin_home: Path) -> None:
    """A name cannot add lines of its own to profile.md."""
    save_onboarding_profile(
        {"first_name": "Ada\n- Occupation: Admin\x1b[31m"},
        complete=True,
        home=aldwin_home,
    )
    profile = (aldwin_home / "memory" / "profile.md").read_text("utf-8")
    assert "- First name: Ada - Occupation: Admin[31m" in profile
    assert profile.count("\n- ") == 1


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"personality": ["sarcastic"]}, "sarcastic"),
        ({"personality": "concise"}, "list"),
        ({"first_name": 5}, "first_name"),
    ],
)
def test_bad_profile_answers_are_refused(
    aldwin_home: Path, fields: dict[str, Any], message: str
) -> None:
    with pytest.raises(SettingsError, match=message):
        save_onboarding_profile(fields, home=aldwin_home)
    assert not (aldwin_home / "state" / "onboarding.json").exists()


# The host starts only once keys work


def test_fresh_home_starts_no_host(aldwin_home: Path, tmp_path: Path) -> None:
    """TC-01: no keys, no host; the socket ticket waits."""
    with _web(aldwin_home, tmp_path) as (client, runtime):
        status = _status(client)
        assert status["keys_ok"] is False and status["host_ready"] is False
        assert _ticket_status(client) == 503
        assert runtime._host is None


def test_skipping_the_profile_never_starts_a_host(
    aldwin_home: Path, tmp_path: Path
) -> None:
    """TC-10: the profile is not a key."""
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        response = client.post("/v1/setup/profile", json={"skipped": True})
        assert response.status_code == 200
        assert response.json()["host_ready"] is False
        assert _ticket_status(client) == 503


def test_saving_a_provider_key_starts_the_host(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-14, TC-25: a checked key opens the gate and the ticket stops waiting."""
    _check_returns(monkeypatch, AccessStatus.OK)
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        response = client.post(
            "/v1/setup/provider-key", json={"name": "openai", "key": OPENAI_KEY}
        )
        assert response.status_code == 200
        status = _status(client)
        assert status["keys_ok"] is True and status["host_ready"] is True
        assert OPENAI_KEY not in json.dumps(status)
        assert _ticket_status(client) == 200


def test_setup_save_starts_the_host(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-15, TC-05: the terminal's first-run answers, saved from the browser."""
    _check_returns(monkeypatch, AccessStatus.OK)
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        response = client.post("/v1/setup/save", json={"openai_api_key": OPENAI_KEY})
        assert response.status_code == 200
        assert response.json()["host_ready"] is True


def test_a_rejected_key_saves_nothing_and_starts_nothing(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-17."""
    _check_returns(monkeypatch, AccessStatus.INVALID_KEY)
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        response = client.post(
            "/v1/setup/provider-key", json={"name": "openai", "key": OPENAI_KEY}
        )
        assert response.status_code == 400
        assert _status(client)["host_ready"] is False
        assert load_secrets(aldwin_home).openai_api_key is None


def test_a_local_model_starts_the_host(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-16: picking a model on this computer passes without a cloud key."""

    async def models(provider: str, home: Path | None = None) -> list[ModelInfo]:
        return [ModelInfo("llama3", "Llama 3", 8192, True, True)]

    monkeypatch.setattr("aldwin.client.list_models_for", models)
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        listed = client.post("/v1/setup/models", json={"provider": "ollama"})
        model = f"ollama/{listed.json()['items'][0]['id']}"
        response = client.post("/v1/setup/save", json={"model": model})
        assert response.status_code == 200
        assert response.json()["is_local"] is True
        assert response.json()["host_ready"] is True
    assert load_config(aldwin_home).model == "ollama/llama3"


def test_another_server_is_added_then_its_model_starts_the_host(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-29: an OpenAI-compatible server by address, with an optional key."""

    class Answers:
        async def check_access(self) -> AccessStatus:
            return AccessStatus.OK

    monkeypatch.setattr(
        "aldwin.core.providers.keys.get_backend", lambda *a, **k: Answers()
    )
    _write_openai_key(aldwin_home)
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        response = client.post(
            "/v1/setup/provider-key",
            json={
                "name": "mybox",
                "base_url": "http://127.0.0.1:1234/v1/",
                "key": "box-key-0123456789",
            },
        )
        assert response.status_code == 200, response.text
        assert "box-key-0123456789" not in response.text
        saved = client.post("/v1/setup/save", json={"model": "mybox/qwen"})
        assert saved.json()["host_ready"] is True

    provider = load_config(aldwin_home).providers["mybox"]
    assert provider.base_url == "http://127.0.0.1:1234/v1"
    assert provider.local is True
    secrets = load_secrets(aldwin_home)
    assert secrets.get_secret("mybox") == "box-key-0123456789"
    assert secrets.get_secret("openai") == OPENAI_KEY


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        ({"name": "openai", "base_url": "http://127.0.0.1:1"}, "already built in"),
        ({"name": "My Box", "base_url": "http://127.0.0.1:1"}, "lowercase"),
        ({"name": "box", "base_url": "ftp://box"}, "http://"),
        ({"name": "box", "base_url": "http://example.com"}, "https://"),
    ],
)
def test_a_bad_server_is_refused_before_any_check(
    aldwin_home: Path, tmp_path: Path, body: dict[str, str], detail: str
) -> None:
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        response = client.post("/v1/setup/provider-key", json=body)
        assert response.status_code == 400
        assert detail in response.json()["detail"]
    assert "box" not in load_config(aldwin_home).providers


def test_a_server_that_wants_a_key_says_so(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No key was typed, so the answer asks for one instead of saying it was wrong."""

    class NeedsKey:
        async def check_access(self) -> AccessStatus:
            return AccessStatus.INVALID_KEY

    monkeypatch.setattr(
        "aldwin.core.providers.keys.get_backend", lambda *a, **k: NeedsKey()
    )
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        response = client.post(
            "/v1/setup/provider-key",
            json={"name": "mybox", "base_url": "http://127.0.0.1:1234/v1"},
        )
        assert response.status_code == 400
        assert response.json()["detail"].endswith("needs an API key.")
    assert "mybox" not in load_config(aldwin_home).providers


def test_unreadable_settings_leave_setup_up_without_a_trace(
    aldwin_home: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A broken config.yaml is the user's file to fix: one warning, no stack trace."""
    (aldwin_home / "config.yaml").write_text("limits: [\n", encoding="utf-8")
    runtime = WebHostRuntime(home=aldwin_home)
    assert asyncio.run(runtime.start_host()) is False
    assert "Could not read settings" in caplog.text
    assert "Traceback" not in caplog.text
    assert runtime._host is None


def test_a_key_or_an_address_is_required(aldwin_home: Path, tmp_path: Path) -> None:
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        response = client.post("/v1/setup/provider-key", json={"name": "openai"})
        assert response.status_code == 422


def test_theme_never_rides_along_with_setup(aldwin_home: Path, tmp_path: Path) -> None:
    """TC-30: the browser's appearance stays in the browser."""
    before = (aldwin_home / "config.yaml").read_text("utf-8")
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        response = client.post("/v1/setup/save", json={"theme": "light"})
        assert response.status_code == 422
    assert (aldwin_home / "config.yaml").read_text("utf-8") == before


def test_starting_twice_runs_one_host(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-18: every setup change may ask; only the first starts a host."""
    _write_openai_key(aldwin_home)
    with _web(aldwin_home, tmp_path) as (client, runtime):
        first = runtime._host
        assert first is not None
        assert client.portal is not None
        assert client.portal.call(runtime.start_host) is True
        assert runtime._host is first


def test_working_keys_at_launch_start_the_host_straight_away(
    aldwin_home: Path, tmp_path: Path
) -> None:
    """TC-19, TC-03: keys set up in the terminal; no setup page needed."""
    _write_openai_key(aldwin_home)
    with _web(aldwin_home, tmp_path) as (client, runtime):
        status = _status(client)
        assert status["host_ready"] is True and status["profile_done"] is False
        assert _ticket_status(client) == 200
        host = runtime._host
    serve_task = getattr(host, "_serve_task", None)
    assert serve_task is not None and serve_task.done()


def test_skipping_the_profile_with_keys_reaches_aldwin(
    aldwin_home: Path, tmp_path: Path
) -> None:
    """TC-02: keys work and the profile is skipped, so both gates are clear."""
    _write_openai_key(aldwin_home)
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        status = client.post("/v1/setup/profile", json={"skipped": True}).json()
        assert status["keys_ok"] and status["profile_done"] and status["host_ready"]


def test_returning_users_skip_what_they_finished(
    aldwin_home: Path, tmp_path: Path
) -> None:
    """TC-04, TC-28: a second launch remembers both gates; lost keys reopen one."""
    _write_openai_key(aldwin_home)
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        client.post("/v1/setup/profile", json={"complete": True})

    with _web(aldwin_home, tmp_path) as (client, _runtime):
        status = _status(client)
        assert status["keys_ok"] and status["profile_done"] and status["host_ready"]

    (aldwin_home / "secrets.yaml").write_text("{}\n", encoding="utf-8")
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        status = _status(client)
        assert status["keys_ok"] is False and status["profile_done"] is True
        assert status["host_ready"] is False


def test_the_terminal_reads_what_the_browser_saved(
    aldwin_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TC-31: one home, one set of files."""
    _check_returns(monkeypatch, AccessStatus.OK)
    with _web(aldwin_home, tmp_path) as (client, _runtime):
        client.post(
            "/v1/setup/provider-key", json={"name": "openai", "key": OPENAI_KEY}
        )
        client.post(
            "/v1/setup/profile",
            json={"first_name": "Ada", "personality": ["direct"], "complete": True},
        )

    status = get_onboarding_status(aldwin_home)
    assert status.has_openai_key and status.openai_source == "file"
    assert "Ada" in read_profile(aldwin_home)[0]
    assert "Be direct" in read_persona(aldwin_home)


def test_privacy_doc_explains_the_profile_seed() -> None:
    """TC-27: what the profile step writes, and that it never overwrites."""
    docs = Path(__file__).resolve().parent.parent / "docs"
    privacy = (docs / "privacy.md").read_text(encoding="utf-8")
    assert "onboarding.json" in privacy
    assert "only while those files still hold what Aldwin shipped" in privacy
    assert "never overwritten" in privacy


def test_a_host_that_fails_to_start_leaves_setup_up(
    aldwin_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken start is logged; the server keeps answering and can try again."""
    _write_openai_key(aldwin_home)

    import aldwin.client as door_client

    real_start = door_client.start_host

    async def broken(*_: Any, **__: Any) -> None:
        raise RuntimeError("model could not be built")

    monkeypatch.setattr(door_client, "start_host", broken)
    with _web(aldwin_home, tmp_path) as (client, runtime):
        status = _status(client)
        assert status["keys_ok"] is True and status["host_ready"] is False
        assert _ticket_status(client) == 503
        monkeypatch.setattr(door_client, "start_host", real_start)
        assert client.portal is not None
        assert client.portal.call(runtime.start_host) is True
        assert _ticket_status(client) == 200
    assert "Could not start Aldwin for the browser" in caplog.text
