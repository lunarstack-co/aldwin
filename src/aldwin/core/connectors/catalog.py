"""The built-in list of apps a user can connect from `/connectors`."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from aldwin.core.connectors.config import (
    AuthKind,
    OAuthSettings,
    check_url,
    is_valid_name,
)


class CatalogEntry(BaseModel):
    """One app in the catalogue."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: StrictStr
    title: StrictStr
    purpose: StrictStr
    url: StrictStr
    auth: AuthKind
    docs_url: StrictStr
    keywords: list[str] = Field(default_factory=list)
    oauth: OAuthSettings | None = None
    token_help_url: StrictStr | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not is_valid_name(value):
            raise ValueError(f"invalid connector name {value!r}")
        return value

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        check_url(value)
        return value


@lru_cache(maxsize=1)
def load_catalog() -> tuple[CatalogEntry, ...]:
    """The catalogue shipped with Aldwin, in display order."""
    raw = (
        files("aldwin.core.assets")
        .joinpath("connectors/catalog.yaml")
        .read_text(encoding="utf-8")
    )
    entries = tuple(
        CatalogEntry.model_validate(item) for item in yaml.safe_load(raw)["entries"]
    )
    names = [entry.name for entry in entries]
    if len(set(names)) != len(names):
        raise ValueError("catalog.yaml has duplicate names")
    return entries


def catalog_entry(name: str) -> CatalogEntry | None:
    return next((e for e in load_catalog() if e.name == name), None)
