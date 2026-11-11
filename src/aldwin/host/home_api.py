"""Read and write conversations, settings and keys in the Aldwin home.

These never start a host, so a door can show settings and run setup before
any model key exists. They use the same files and locks as the terminal.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import SecretStr, ValidationError

from aldwin.core.home import DIR_MODE, FILE_MODE, atomic_write, home_dir
from aldwin.core.settings import (
    AldwinConfig,
    ConfigError,
    LimitsConfig,
    ResolvedKey,
    SearchConfig,
    SecretsConfig,
    load_config,
    mask_secret,
    normalize_key_field,
    resolve_key,
    save_secrets,
    update_config_check_for_updates,
    update_config_limits,
    update_config_model,
    update_config_search_provider,
    update_config_searxng_url,
)
from aldwin.host.sessions import SESSIONS_SUBDIR, SessionStore

KeyKind = Literal["provider", "search"]
KeyStatusName = Literal["ok", "rejected", "unreachable", "unknown"]

PATCHABLE_SETTINGS: Final[tuple[str, ...]] = (
    "check_for_updates",
    "limits",
    "model",
    "search",
)
_SEARCH_FIELDS: Final[frozenset[str]] = frozenset({"provider", "searxng_url"})
_SECRET_SUFFIXES: Final[tuple[str, ...]] = ("_key", "_token", "_secret")
ONBOARDING_FILE: Final[str] = "onboarding.json"
PROFILE_TEXT_FIELDS: Final[tuple[str, ...]] = ("first_name", "occupation")
MAX_PROFILE_CHARS: Final[int] = 200
# Fixed sentences, so nothing the user types reaches persona.md.
PERSONALITY_STYLES: Final[dict[str, str]] = {
    "concise": "Keep answers short, and expand only when asked.",
    "detailed": "Give thorough answers with the reasoning behind them.",
    "friendly": "Be warm and conversational.",
    "direct": "Be direct and skip pleasantries.",
    "teaching": "Explain the why, and teach as you go.",
}
ONBOARDING_VERSION: Final[int] = 1
_SERVER_NAME_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")


class SettingsError(ValueError):
    """Settings could not be read or changed; the message says why, with no secret."""


@dataclass(frozen=True, slots=True)
class KeyOutcome:
    """Result of checking or saving an API key, safe to show in a door."""

    ok: bool
    status: KeyStatusName
    message: str
    masked: str = ""
    # True when an environment variable still wins over the key just saved.
    env_override: bool = False


def list_conversation_metas(home: Path | None = None) -> list[dict[str, Any]]:
    """Return saved conversations newest first, leaving out ones with no turns."""
    store = SessionStore(_resolve_home(home) / SESSIONS_SUBDIR)
    rows = []
    for meta in store.list_metas():
        row = asdict(meta)
        del row["has_turns"]
        rows.append(row)
    return rows


def load_conversation_json(
    session_id: str, home: Path | None = None
) -> dict[str, Any] | None:
    """Return a conversation file as saved, or ``None`` if missing or corrupt."""
    store = SessionStore(_resolve_home(home) / SESSIONS_SUBDIR)
    stored = store.load_id(session_id)
    if stored is None:
        return None
    try:
        raw: Any = json.loads(stored.path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def get_settings_view(home: Path | None = None) -> dict[str, Any]:
    """Return the settings a door may show; keys appear masked, never in full."""
    root = _resolve_home(home)
    config = _load_config(root)
    return {
        "model": config.model,
        "check_for_updates": config.check_for_updates,
        "limits": config.limits.model_dump(),
        "search": {
            "provider": config.search.provider,
            "searxng_url": config.search.searxng_url,
        },
        "keys": _key_rows(config, root),
    }


def apply_settings_patch(
    patch: Mapping[str, Any], home: Path | None = None
) -> dict[str, Any]:
    """Apply the allowed fields in ``patch`` and return the updated settings.

    Every field is checked before anything is written, so a refused patch
    changes nothing. Raises ``SettingsError`` naming the bad field.
    """
    root = _resolve_home(home)
    _refuse_unknown_fields(patch)
    config = _load_config(root)
    limits = _checked_limits(patch.get("limits"), config) if "limits" in patch else {}
    search = _checked_search(patch.get("search")) if "search" in patch else {}
    if "model" in patch:
        _check_model(patch["model"], config, root)
    if "check_for_updates" in patch and not isinstance(
        patch["check_for_updates"], bool
    ):
        raise SettingsError("check_for_updates must be true or false.")

    try:
        if "model" in patch:
            update_config_model(patch["model"], root)
        if limits:
            update_config_limits(limits, root)
        if "provider" in search:
            update_config_search_provider(search["provider"], root)
        if "searxng_url" in search:
            update_config_searxng_url(search["searxng_url"], root)
        if "check_for_updates" in patch:
            update_config_check_for_updates(patch["check_for_updates"], root)
    except ConfigError as exc:
        raise SettingsError(str(exc)) from None
    return get_settings_view(root)


async def check_key(
    kind: KeyKind,
    name: str,
    key: str,
    *,
    home: Path | None = None,
    base_url: str | None = None,
) -> KeyOutcome:
    """Check an API key with its provider without saving it."""
    root = _resolve_home(home)
    provider = name.strip().lower()
    if kind == "search":
        return await _check_search_key(provider, key)
    return await _check_provider_key(provider, key, root, base_url)


async def save_key(
    kind: KeyKind,
    name: str,
    key: str,
    *,
    home: Path | None = None,
    base_url: str | None = None,
) -> KeyOutcome:
    """Check an API key and save it to ``secrets.yaml`` only if the check passes.

    Saving a search key also makes that provider the active one, as ``/search``
    does in the terminal.
    """
    root = _resolve_home(home)
    provider = name.strip().lower()
    outcome = await check_key(kind, provider, key, home=root, base_url=base_url)
    if not outcome.ok:
        return outcome
    field = (
        normalize_key_field(provider)
        if kind == "search"
        else _provider_secret_field(provider, root)
    )
    secrets = SecretsConfig.model_validate({field: SecretStr(key.strip())})
    await asyncio.to_thread(save_secrets, secrets, root)
    if kind == "search":
        # Matches `/search <name> <key>`: a newly connected provider is the one used.
        await asyncio.to_thread(update_config_search_provider, provider, root)
    resolved = _resolved_key(kind, provider, root)
    return KeyOutcome(
        ok=True,
        status="ok",
        message=outcome.message,
        masked=mask_secret(key),
        env_override=resolved.source == "env" and resolved.key != key.strip(),
    )


def keys_gate_ok(home: Path | None = None) -> bool:
    """Return whether a model can run: an OpenAI key, the model's own key, or local.

    This is the rule the terminal uses to skip first-run setup.
    """
    from aldwin.host.in_process import get_onboarding_status

    try:
        status = get_onboarding_status(_resolve_home(home))
    except ConfigError as exc:
        raise SettingsError(str(exc)) from None
    return _model_can_run(asdict(status))


def get_setup_status(home: Path | None = None) -> dict[str, Any]:
    """Return which first-run steps are done; keys appear masked, never in full."""
    from aldwin.host.in_process import get_onboarding_status

    root = _resolve_home(home)
    try:
        status = asdict(get_onboarding_status(root))
    except ConfigError as exc:
        raise SettingsError(str(exc)) from None
    status["keys_ok"] = _model_can_run(status)
    record = _read_onboarding(root) or {}
    skipped = bool(record.get("skipped"))
    status["profile_done"] = bool(record.get("complete")) or skipped
    status["profile_skipped"] = skipped
    status["profile"] = {
        "first_name": record.get("first_name", ""),
        "occupation": record.get("occupation", ""),
        "personality": list(record.get("personality", [])),
    }
    return status


def save_onboarding_profile(
    fields: Mapping[str, Any],
    *,
    complete: bool = False,
    skipped: bool = False,
    home: Path | None = None,
) -> dict[str, Any]:
    """Save the profile step to ``state/onboarding.json``.

    Fields are kept as drafts until ``complete``; ``skipped`` clears them. On
    completion the answers are added to ``memory/profile.md`` and
    ``persona.md``, but only while those still hold the shipped defaults.
    """
    root = _resolve_home(home)
    drafts = _checked_profile_fields(fields)
    if skipped:
        record: dict[str, Any] = {"skipped": True, "complete": False}
    else:
        record = {
            k: v
            for k, v in (_read_onboarding(root) or {}).items()
            if k in (*PROFILE_TEXT_FIELDS, "personality", "complete")
        }
        record.update(drafts)
        record["complete"] = complete or bool(record.get("complete"))
        record["skipped"] = False
    record["version"] = ONBOARDING_VERSION
    record["saved_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    path = root / "state" / ONBOARDING_FILE
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    atomic_write(path, json.dumps(record, indent=2) + "\n", mode=FILE_MODE)
    if complete and not skipped:
        _seed_profile(root, record)
        _seed_persona(root, record.get("personality", []))
    return record


async def connect_server(
    name: str,
    base_url: str,
    key: str | None = None,
    *,
    home: Path | None = None,
) -> KeyOutcome:
    """Add an OpenAI-compatible server once it answers; any key is saved with it."""
    from aldwin.core.providers.base import AccessStatus
    from aldwin.core.providers.keys import ServerAddressError, connect_provider_server
    from aldwin.core.providers.registry import get_provider

    root = _resolve_home(home)
    cleaned = name.strip().lower()
    if not _SERVER_NAME_RE.fullmatch(cleaned):
        return KeyOutcome(
            False,
            "unknown",
            "Name the server with lowercase letters, digits, '-' or '_' "
            "(up to 32 characters).",
        )
    builtin = get_provider(cleaned)
    if builtin is not None:
        return KeyOutcome(
            False,
            "unknown",
            f"{builtin.title} is already built in. Pick another name for this server.",
        )
    address = base_url.strip().rstrip("/")
    try:
        status = await connect_provider_server(cleaned, address, key, home=root)
    except ServerAddressError as exc:
        return KeyOutcome(False, "rejected", str(exc))
    if status == AccessStatus.OK:
        return KeyOutcome(
            True, "ok", f"Connected to {address}.", masked=mask_secret(key)
        )
    if status == AccessStatus.INVALID_KEY:
        reason = "rejected the key" if key else "needs an API key"
        return KeyOutcome(False, "rejected", f"The server at {address} {reason}.")
    return KeyOutcome(
        False, "unreachable", f"Could not connect to server at {address}."
    )


def _model_can_run(status: Mapping[str, Any]) -> bool:
    return bool(
        status["has_openai_key"] or status["has_provider_key"] or status["is_local"]
    )


def _resolve_home(home: Path | None) -> Path:
    return (home if home is not None else home_dir()).expanduser().resolve()


def _load_config(root: Path) -> AldwinConfig:
    try:
        return load_config(root)
    except ConfigError as exc:
        raise SettingsError(str(exc)) from None


def _refuse_unknown_fields(patch: Mapping[str, Any]) -> None:
    for name in patch:
        if name in PATCHABLE_SETTINGS:
            continue
        if name.lower().endswith(_SECRET_SUFFIXES):
            raise SettingsError(
                f"{name} is a key. Save keys with POST /v1/settings/secrets."
            )
        raise SettingsError(
            f"{name} cannot be changed here. Allowed: {', '.join(PATCHABLE_SETTINGS)}."
        )


def _checked_limits(value: Any, config: AldwinConfig) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SettingsError("limits must be an object.")
    known = set(LimitsConfig.model_fields)
    for key in value:
        if key not in known:
            raise SettingsError(
                f"limits.{key} is not a limit. Allowed: {', '.join(sorted(known))}."
            )
    try:
        LimitsConfig.model_validate({**config.limits.model_dump(), **value})
    except ValidationError as exc:
        error = exc.errors()[0]
        where = ".".join(str(part) for part in error.get("loc", ()))
        raise SettingsError(f"limits.{where}: {error.get('msg', '')}.") from None
    return dict(value)


def _checked_search(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SettingsError("search must be an object.")
    for key in value:
        if key not in _SEARCH_FIELDS:
            raise SettingsError(
                f"search.{key} cannot be changed here. "
                f"Allowed: {', '.join(sorted(_SEARCH_FIELDS))}."
            )
    url = value.get("searxng_url")
    if url is not None and not isinstance(url, str):
        raise SettingsError("search.searxng_url must be text or null.")
    if "provider" in value:
        try:
            SearchConfig.model_validate({"provider": value["provider"]})
        except ValidationError as exc:
            message = exc.errors()[0].get("msg", "")
            raise SettingsError(f"search.provider: {message}.") from None
    return dict(value)


def _check_model(value: Any, config: AldwinConfig, root: Path) -> None:
    from aldwin.core.providers.registry import validate_model_ref

    if not isinstance(value, str) or not value.strip():
        raise SettingsError("model must be a non-empty model name.")
    try:
        validate_model_ref(value.strip(), config, file=root / "config.yaml")
    except ConfigError as exc:
        raise SettingsError(str(exc)) from None


def _key_rows(config: AldwinConfig, root: Path) -> list[dict[str, Any]]:
    from aldwin.core.providers.registry import list_providers
    from aldwin.core.tools.web_search.shared import hosted_provider_classes

    rows: list[dict[str, Any]] = []
    for entry in list_providers(config):
        if entry.local or not entry.secret:
            continue
        resolved = _resolved_key("provider", entry.name, root, entry.secret)
        rows.append(_key_row("provider", entry.name, entry.title, resolved))
    for name, cls in hosted_provider_classes().items():
        resolved = _resolved_key("search", name, root)
        rows.append(_key_row("search", name, cls.name, resolved))
    return rows


def _resolved_key(
    kind: KeyKind, name: str, root: Path, secret: str | None = None
) -> ResolvedKey:
    """Find the key in use for ``name``, checking the environment first."""
    from aldwin.core.providers.registry import get_provider
    from aldwin.core.tools.web_search.connect import env_key
    from aldwin.core.tools.web_search.shared import hosted_provider_classes

    if kind == "provider":
        custom = get_provider(name) is None and secret is not None
        return resolve_key(name, root, secret_field=secret if custom else None)
    cls = hosted_provider_classes().get(name)
    env = env_key(cls) if cls is not None else None
    if env is not None:
        return ResolvedKey(key=env[1], source="env")
    return resolve_key(name, root)


def _key_row(
    kind: KeyKind, name: str, title: str, resolved: ResolvedKey
) -> dict[str, Any]:
    return {
        "kind": kind,
        "name": name,
        "title": title,
        "source": resolved.source,
        "masked": mask_secret(resolved.key),
    }


def _provider_secret_field(provider: str, root: Path) -> str:
    from aldwin.core.providers.keys import resolve_provider_secret_field

    return resolve_provider_secret_field(provider, home=root)


async def _check_provider_key(
    provider: str, key: str, root: Path, base_url: str | None
) -> KeyOutcome:
    from aldwin.core.providers.base import AccessStatus
    from aldwin.core.providers.keys import check_provider_key
    from aldwin.core.providers.registry import get_provider

    config = _load_config(root)
    entry = get_provider(provider, config)
    if entry is None:
        return KeyOutcome(False, "unknown", f"Unknown model provider '{provider}'.")
    if entry.local or not entry.secret:
        return KeyOutcome(False, "unknown", f"{entry.title} does not use an API key.")
    status = await check_provider_key(
        provider, key, home=root, base_url=base_url, config=config
    )
    if status == AccessStatus.OK:
        return KeyOutcome(True, "ok", f"{entry.title} accepted the key.")
    if status == AccessStatus.INVALID_KEY:
        return KeyOutcome(
            False,
            "rejected",
            f"Your {entry.title} key was rejected. Check the key and try again.",
        )
    if status == AccessStatus.NO_ACCESS:
        return KeyOutcome(
            False,
            "rejected",
            f"Your {entry.title} key works but has no access to its models. "
            "Check the account, then try again.",
        )
    return KeyOutcome(
        False, "unreachable", f"Could not reach {entry.title} to verify the key."
    )


async def _check_search_key(provider: str, key: str) -> KeyOutcome:
    from aldwin.core.tools.web_search.connect import check_search_key
    from aldwin.core.tools.web_search.hosted import KeyCheck
    from aldwin.core.tools.web_search.shared import hosted_provider_classes
    from aldwin.host.runner import key_check_error

    cls = hosted_provider_classes().get(provider)
    if cls is None:
        return KeyOutcome(False, "unknown", f"Unknown search provider '{provider}'.")
    result = await check_search_key(provider, key)
    if result.outcome is KeyCheck.OK:
        return KeyOutcome(True, "ok", f"{cls.name} accepted the key.")
    status: KeyStatusName = (
        "rejected"
        if result.outcome in (KeyCheck.REJECTED, KeyCheck.NO_CREDITS)
        else "unreachable"
    )
    return KeyOutcome(False, status, key_check_error(cls.name, provider, result)[1])


def _checked_profile_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    checked: dict[str, Any] = {}
    for name in PROFILE_TEXT_FIELDS:
        value = fields.get(name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise SettingsError(f"{name} must be text.")
        cleaned = _one_line(value)
        if len(cleaned) > MAX_PROFILE_CHARS:
            raise SettingsError(
                f"{name} must be at most {MAX_PROFILE_CHARS} characters."
            )
        checked[name] = cleaned
    styles = fields.get("personality")
    if styles is not None:
        if not isinstance(styles, list) or not all(isinstance(s, str) for s in styles):
            raise SettingsError("personality must be a list of styles.")
        unknown = [s for s in styles if s not in PERSONALITY_STYLES]
        if unknown:
            raise SettingsError(
                f"Unknown personality {unknown[0]!r}. "
                f"Choose from: {', '.join(PERSONALITY_STYLES)}."
            )
        checked["personality"] = list(dict.fromkeys(styles))
    return checked


def _one_line(value: str) -> str:
    """Drop control and format characters and fold whitespace to single spaces."""
    kept = "".join(
        " " if ch.isspace() else ch
        for ch in value
        if ch.isspace() or not unicodedata.category(ch).startswith("C")
    )
    return " ".join(kept.split())


def _seed_profile(root: Path, record: Mapping[str, Any]) -> None:
    from aldwin.core.memory import PROFILE_REL_PATH, read_profile

    if read_profile(root)[0].strip():
        return
    lines = [
        f"- {label}: {record[key]}"
        for key, label in (("first_name", "First name"), ("occupation", "Occupation"))
        if record.get(key)
    ]
    if not lines:
        return
    path = root / PROFILE_REL_PATH
    try:
        existing = path.read_text(encoding="utf-8").rstrip()
    except OSError:
        existing = ""
    body = "\n".join(lines)
    text = f"{existing}\n\n{body}\n" if existing else f"{body}\n"
    path.parent.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    atomic_write(path, text, mode=FILE_MODE)


def _seed_persona(root: Path, styles: list[str]) -> None:
    from aldwin.core.persona import (
        PERSONA_FILENAME,
        read_default_persona,
        write_persona,
    )

    if not styles:
        return
    default = read_default_persona()
    try:
        current = (root / PERSONA_FILENAME).read_text(encoding="utf-8").strip()
    except OSError:
        current = ""
    if current and current != default:
        return
    wishes = "\n".join(f"- {PERSONALITY_STYLES[s]}" for s in styles)
    write_persona(root, f"{default}\n\n## How the user likes answers\n\n{wishes}")


def _read_onboarding(root: Path) -> dict[str, Any] | None:
    try:
        raw: Any = json.loads(
            (root / "state" / ONBOARDING_FILE).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None
