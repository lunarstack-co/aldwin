# Import fixtures

One file per app in `src/aldwin/core/assets/connectors/import_sources/`, named
after the source file with `-` replaced by `_`. Every value is fake. Each file
was written against the shape named here, checked on 1 Nov 2026.

| Fixture | Checked against |
|---|---|
| `claude_code.json` | Claude Code docs, "MCP" page: scopes, `type` values, `${VAR}` references |
| `claude_desktop.json` | modelcontextprotocol.io, "Connect to local MCP servers" |
| `cline.json` | `cline/cline`: `sdk/packages/core/src/extensions/mcp/types.ts`, `sdk/packages/shared/src/storage/paths.ts` |
| `codex.toml` | Codex docs, "MCP" page: `mcp_servers` tables and their keys |
| `copilot_cli.json` | GitHub docs, "Add MCP servers" for Copilot CLI |
| `cursor.json` | Cursor docs, "MCP" page |
| `devin.json` | Devin docs, "MCP configuration" |
| `gemini_cli.json` | Gemini CLI docs, "MCP servers": `url` is SSE, `httpUrl` is HTTP |
| `goose.yaml` | `aaif-goose/goose`: `crates/goose/src/agents/extension.rs`, `config/extensions.rs` |
| `hermes_agent.yaml` | `NousResearch/hermes-agent`: `cli-config.yaml.example`, `mcp_servers` block |
| `nanobot.json` | `HKUDS/nanobot`: `nanobot/config/schema.py`, `MCPServerConfig` and `ToolsConfig` |
| `opencode.jsonc` | opencode docs, "MCP servers" and "Config" pages |
| `pi.json` | `earendil-works/pi`: `packages/coding-agent/docs/mcp.md` |
| `sse.json` | A minimal standard file with one SSE entry |
| `vscode.jsonc` | VS Code docs, "MCP configuration reference" |
| `zed.json` | Zed docs, "MCP"; the `{path, args, env}` form is Zed's older spelling |
