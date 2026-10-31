"""Constants and user-facing messages for connector sign-in."""

from __future__ import annotations

from typing import Final

CALLBACK_PORTS: Final[tuple[int, ...]] = (47823, 47824, 47825)
CALLBACK_PATH: Final[str] = "/callback"
DEFAULT_CLIENT_METADATA_URL: Final[str] = "https://askaldwin.com/oauth/client.json"
DISCOVERY_TIMEOUT_S: Final[float] = 15.0
SIGN_IN_TIMEOUT_S: Final[float] = 300.0
REFRESH_SKEW_S: Final[float] = 60.0
REFRESH_FENCE_TIMEOUT_S: Final[float] = 30.0


def needs_registration(title: str, name: str) -> str:
    return (
        f"{title} needs an app registration Aldwin doesn't have. Add a client ID "
        f"with: aldwin connectors add {name} --url … --client-id …"
    )


def registration_refused(title: str, name: str) -> str:
    return (
        f"{title} refused Aldwin's app registration. Try again later or add a "
        f"client ID: aldwin connectors add {name} --url … --client-id …"
    )


def pkce_unsupported(title: str) -> str:
    return f"{title}'s sign-in server doesn't support a secure sign-in (PKCE)."


def ports_busy() -> str:
    return (
        "Ports 47823-47825 are busy; close the app using them or use the paste option."
    )


def needs_sign_in(title: str) -> str:
    """Shown when a flow needs a person but has no way to ask one."""
    return f"{title} needs you to sign in: /connectors."


def needs_sign_in_notice(title: str) -> str:
    return f"{title} needs you to sign in again: /connectors."


def needs_sign_in_tool_error(title: str) -> str:
    return f"{title} needs you to sign in again. Ask the user to run /connectors."


def sign_in_declined(title: str) -> str:
    return f"{title} sign-in was declined in the browser. Try again from /connectors."


def issuer_mismatch(title: str) -> str:
    return (
        f"{title}'s sign-in server sent a response for a different issuer. Try "
        f"again; if it repeats, report it to {title}."
    )


def no_browser_sign_in(title: str, name: str) -> str:
    return (
        f"{title} doesn't offer browser sign-in. Add it with a token instead: "
        f"aldwin connectors add {name} --url … --token-env VAR."
    )


def no_sign_in_needed(title: str) -> str:
    return f"{title} doesn't need sign-in, so it connected without it."


def nothing_waiting(name: str) -> str:
    return f"No sign-in is waiting for {name}."


PASTE_NO_CODE: Final[str] = (
    "That address has no sign-in code. Paste the full address your browser ended on."
)
PASTE_WRONG_STATE: Final[str] = (
    "That address is from a different sign-in. Paste the address from this sign-in."
)
