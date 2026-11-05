#!/usr/bin/env python3
"""Record an HTTP fixture for a model provider using Agentling's RecordingTransport.

Usage:
    uv run python scripts/record_provider_fixture.py <provider> <model_id> [output_file]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx
from agentling import Model
from agentling.testing import RecordingTransport, check_model, write_fixture

from aldwin.core.providers.factory import get_backend
from aldwin.core.providers.registry import load_builtin_providers
from aldwin.protocol.secret_shapes import redact_pasted_secrets


def sanitize_exchange(exchange: object) -> None:
    """Scrub sensitive credentials from headers and body before writing fixture."""
    req = getattr(exchange, "request", None)
    if not isinstance(req, dict):
        return

    headers = req.get("headers", {})
    if isinstance(headers, dict):
        for k in list(headers.keys()):
            if k.lower() in ("authorization", "x-api-key", "api-key"):
                headers[k] = "‹redacted›"

    body = req.get("body")
    if isinstance(body, str):
        req["body"] = redact_pasted_secrets(body)
    elif isinstance(body, dict):
        dumped = json.dumps(body)
        cleaned = redact_pasted_secrets(dumped)
        if cleaned != dumped:
            req["body"] = json.loads(cleaned)


async def record_fixture(provider_name: str, model_id: str, dest_path: Path) -> None:
    providers = {p.name: p for p in load_builtin_providers()}
    entry = providers.get(provider_name.lower())
    if entry is None:
        known = ", ".join(sorted(providers))
        sys.stderr.write(f"Error: Unknown provider {provider_name!r}. Known: {known}\n")
        sys.exit(1)

    recording = RecordingTransport()

    def factory(transport: httpx.AsyncBaseTransport | None) -> Model:
        return get_backend(entry, transport=recording).build_model(model_id)

    print(f"Recording {provider_name}/{model_id} interaction...")
    try:
        report = await check_model(factory, live=True)
        if not report.ok:
            failed = [r.name for r in report.rules if r.status == "failed"]
            print(f"Warning: Conformance check reported failures: {failed}")
    except Exception as exc:
        print(f"Note: Recorded with exception: {exc}")

    for exchange in recording.exchanges:
        sanitize_exchange(exchange)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    write_fixture(dest_path, recording.exchanges)
    print(f"✓ Recorded {len(recording.exchanges)} exchanges to {dest_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record provider HTTP fixture for test replay."
    )
    parser.add_argument("provider", help="Provider name (e.g. openai, anthropic)")
    parser.add_argument("model", help="Model ID (e.g. gpt-4o-mini)")
    parser.add_argument("output", nargs="?", help="Optional path to output file")

    args = parser.parse_args()
    dest = (
        Path(args.output)
        if args.output
        else Path(f"tests/fixtures/providers/{args.provider.lower()}/fixture.jsonl")
    )

    asyncio.run(record_fixture(args.provider, args.model, dest))


if __name__ == "__main__":
    main()
