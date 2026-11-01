"""Token shapes shared by the chat screen and the host."""

from __future__ import annotations

import pytest

from aldwin.protocol import REDACTED, find_pasted_secrets, redact_pasted_secrets

GITHUB = "ghp_" + "a1B2" * 9
GITHUB_FINE = "github_pat_" + "11ABCDEFG0_" + "x" * 30
SLACK = "xoxb-1234567890-abcdefghij"
API_KEY = "sk-proj-" + "Abc123_x" * 3
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJlLXZhbHVl"
# A JWT whose payload holds a run that also matches the `sk-` shape.
JWT_WITH_INNER_KEY = (
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIi-sk-" + "a" * 24 + ".c2lnbmF0dXJlLXZhbHVl"
)
SAMPLES = (GITHUB, GITHUB_FINE, SLACK, API_KEY, JWT, JWT_WITH_INNER_KEY)


@pytest.mark.parametrize(
    ("sample", "kind"),
    [
        (GITHUB, "github"),
        (GITHUB_FINE, "github"),
        (SLACK, "slack"),
        (API_KEY, "api_key"),
        (JWT, "jwt"),
    ],
)
def test_each_shape_matches_its_sample(sample: str, kind: str) -> None:
    text = f"here it is: {sample} thanks"
    [found] = find_pasted_secrets(text)
    assert found.kind == kind
    assert text[found.start : found.end] == sample


@pytest.mark.parametrize("sample", SAMPLES)
def test_token_at_either_end_is_found(sample: str) -> None:
    assert redact_pasted_secrets(f"{sample} first") == f"{REDACTED} first"
    assert redact_pasted_secrets(f"last {sample}") == f"last {REDACTED}"
    assert redact_pasted_secrets(sample) == REDACTED


@pytest.mark.parametrize(
    "text",
    [
        "",
        "sk-short",
        "ghp_" + "a" * 10,
        "eyJabc.def",
        "task-1234567890abcdefghij",
        "a plain message about GitHub tokens",
    ],
)
def test_near_misses_do_not_match(text: str) -> None:
    assert find_pasted_secrets(text) == []
    assert redact_pasted_secrets(text) == text


def test_redacts_every_match_and_keeps_the_rest() -> None:
    text = f"use {GITHUB} and `{JWT}`, then {SLACK}."
    assert len(find_pasted_secrets(text)) == 3
    assert (
        redact_pasted_secrets(text)
        == f"use {REDACTED} and `{REDACTED}`, then {REDACTED}."
    )


def test_overlapping_shapes_are_one_match() -> None:
    text = f"token {JWT_WITH_INNER_KEY} end"
    [found] = find_pasted_secrets(text)
    assert found.kind == "jwt"
    assert text[found.start : found.end] == JWT_WITH_INNER_KEY
    assert redact_pasted_secrets(text) == f"token {REDACTED} end"


@pytest.mark.parametrize("sample", SAMPLES)
def test_find_and_redact_agree(sample: str) -> None:
    text = f"{sample}\n{API_KEY} and {sample}"
    matches = find_pasted_secrets(text)
    redacted = redact_pasted_secrets(text)
    assert redacted.count(REDACTED) == len(matches)
    for match in matches:
        assert text[match.start : match.end] not in redacted
