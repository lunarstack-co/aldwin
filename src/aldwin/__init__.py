"""Aldwin: an open-source personal assistant built on Agentling."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("aldwin")
except PackageNotFoundError:
    __version__ = "0.0.2.1"


def format_version_string() -> str:
    """Return the `aldwin --version` line, with the protocol and agentling SDK versions.

    Lives at the package root, not in `host`, so `--version` never pays for
    importing the full client/host module graph just to print one line.
    """
    try:
        agentling_version = version("agentling")
    except PackageNotFoundError:
        agentling_version = "unknown"
    return f"aldwin {__version__} (protocol v2, agentling {agentling_version})"


__all__ = ["__version__", "format_version_string"]
