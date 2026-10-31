"""The callback ports and client ID match the hosted client document."""

from __future__ import annotations

import json
from pathlib import Path

from aldwin.core.connectors.errors import ConnectorError, SignInError
from aldwin.core.connectors.oauth_messages import (
    CALLBACK_PATH,
    CALLBACK_PORTS,
    DEFAULT_CLIENT_METADATA_URL,
)

DOC = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "oauth_client.json").read_text("utf-8")
)


def test_every_callback_port_is_registered_for_both_hosts() -> None:
    for port in CALLBACK_PORTS:
        for host in ("127.0.0.1", "localhost"):
            assert f"http://{host}:{port}{CALLBACK_PATH}" in DOC["redirect_uris"]


def test_client_id_is_the_document_url() -> None:
    assert DOC["client_id"] == DEFAULT_CLIENT_METADATA_URL
    assert DOC["token_endpoint_auth_method"] == "none"


def test_sign_in_errors_are_connector_errors() -> None:
    assert issubclass(SignInError, ConnectorError)
