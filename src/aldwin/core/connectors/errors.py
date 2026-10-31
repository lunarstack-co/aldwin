"""Connector errors. Each `message` is shown to the user exactly as written."""

from __future__ import annotations


class ConnectorError(Exception):
    """Base class for connector failures with a user-facing `message`."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConnectorConfigError(ConnectorError):
    """A name or URL the user gave is not acceptable."""


class ConnectorUnreachable(ConnectorError):
    """The server could not be reached."""

    def __init__(self, host: str, reason: str) -> None:
        super().__init__(f"Couldn't reach {host}: {reason}.")


class ConnectorNotMcp(ConnectorError):
    """The address answered, but not as an MCP server."""

    def __init__(self, content_type: str) -> None:
        super().__init__(f"That address isn't an MCP server (got {content_type}).")


class ConnectorAuthRequired(ConnectorError):
    """The server answered 401 and no token is set."""

    def __init__(self, host: str) -> None:
        super().__init__(f"{host} needs a token.")


class ConnectorAuthRejected(ConnectorError):
    """The server answered 401 to the token we sent."""

    def __init__(self) -> None:
        super().__init__("The server refused the token (401).")


class ConnectorSessionLost(ConnectorError):
    """An open session ended: a transport error or `Session terminated`."""


class SignInError(ConnectorError):
    """Browser sign-in could not finish. The message ends with what to do next."""


class NeedsSignIn(SignInError):
    """Only a person can fix this (a 401 that one refresh did not cure)."""


class SignInCancelled(SignInError):
    """The user stopped the sign-in."""

    def __init__(self) -> None:
        super().__init__("Sign-in cancelled.")


class SignInTimedOut(SignInError):
    """Nobody finished signing in within the wait."""

    def __init__(self) -> None:
        super().__init__("Sign-in timed out.")
