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
    """The server answered 401 to the token Aldwin sent."""

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


class ConnectorCommandNotFound(ConnectorError):
    """A local connector's program is not installed or not runnable."""

    def __init__(self, command: str) -> None:
        super().__init__(f"command not found: {command}")


class ConnectorNotApproved(ConnectorError):
    """The user said no to running a local command; nothing was saved or started."""

    def __init__(self, title: str) -> None:
        super().__init__(f"{title} was not approved, so nothing was saved.")


class ConnectorProcessExited(ConnectorSessionLost):
    """A local server's process ended while it was in use."""

    def __init__(self, code: int | None) -> None:
        super().__init__(f"server exited (code {code})")
