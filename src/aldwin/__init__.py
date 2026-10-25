"""Aldwin: an open-source personal assistant built on Agentling."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("aldwin")
except PackageNotFoundError:
    __version__ = "0.0.2"

__all__ = ["__version__"]
