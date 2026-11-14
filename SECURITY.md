# Security policy

## Supported versions

Aldwin is pre-1.0. Security fixes go into the latest release only, so please upgrade before reporting:

```bash
uv tool upgrade aldwin
```

## Reporting a vulnerability

Please do not open a public issue for a security problem. Email [contact@lunarstack.co](mailto:contact@lunarstack.co) with:

- what you found and why it matters
- the steps to reproduce it, or a proof of concept
- your Aldwin version (`aldwin --version`), operating system and Python version

Leave out real keys, tokens and private files.

## What counts

Aldwin runs on your machine without an OS sandbox, and a policy engine checks every tool call. Reports we most want to hear about:

- a way past an approval, the workspace boundary, or the blocked secret paths
- a secret reaching the model, a log, or a saved conversation
- a way to reach `aldwin web` from another computer or another website
- a connector or MCP server gaining more than the user approved

The [safety model](docs/safety.md) lists the rules Aldwin enforces and its known limits.
