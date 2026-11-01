"""The private SDK methods the OAuth provider builds on still exist.

`AldwinOAuthProvider` overrides or calls these; a minor `mcp` release that
renames one should fail here, not in a user's sign-in.
"""

from __future__ import annotations

import inspect

from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.oauth2 import OAuthContext

EXPECTED_PARAMS = {
    "_auth_flow": ["self", "request"],
    "_initialize": ["self"],
    "_refresh_token": ["self"],
    "_handle_refresh_response": ["self", "response"],
    "_add_auth_header": ["self", "request"],
}


def test_overridden_and_called_methods_keep_their_shape() -> None:
    for name, params in EXPECTED_PARAMS.items():
        method = getattr(OAuthClientProvider, name)
        assert list(inspect.signature(method).parameters) == params, name


def test_context_fields_used_by_the_provider() -> None:
    fields = OAuthContext.__dataclass_fields__
    for name in (
        "current_tokens",
        "token_expiry_time",
        "oauth_metadata",
        "client_info",
        "redirect_handler",
        "lock",
    ):
        assert name in fields, name
    for method in ("is_token_valid", "can_refresh_token"):
        assert callable(getattr(OAuthContext, method))
