# Importing connectors from other agent apps

`aldwin connectors import` brings the MCP servers you already set up in another
app into Aldwin. Run it with no path and it looks in the usual places, lists
what it found, and asks before importing:

```
$ aldwin connectors import
Found:
  Claude Desktop (user)  11 servers  /Users/you/Library/Application Support/Claude/claude_desktop_config.json
  Codex CLI (user)  3 servers  /Users/you/.codex/config.toml
Import these? [y/N] y
```

Give a path to import one file, `--yes` to skip the question, and
`--rename-conflicts` to save a taken name as `name-2` instead of skipping it.
In the chat, `/connectors` has the same import under `Import from other apps…`.

## What import does, and never does

- Local commands are saved switched off with the note `review and enable`.
  Nothing runs until you enable the connector and approve the command.
- Every server from a project file (one inside the workspace, such as
  `.mcp.json`) is saved switched off, remote ones included. Those files arrive
  with the repository, so their author may not be you.
- Tokens and environment values move to Aldwin's credential store
  (`~/.aldwin/credentials/mcp/`). They never land in `connectors.yaml`.
- A value that only names a variable, such as `${GITHUB_TOKEN}`, becomes an
  allowed variable name with no value stored. Values Aldwin can't resolve, such
  as editor prompts, are noted as `set NAME by hand`.
- A server that already exists with the same command or URL is skipped with
  `same as <name>`.
- Import only reads. It never starts a program, connects anywhere, or asks for
  an approval.
- A file that can't be read is reported as a warning, and the others still
  import. The command exits 1 only when nothing was imported.

Each app's own settings, such as trust flags, tool lists and timeouts, are not
carried over. Aldwin's own approval rules apply to every imported server.

## Supported apps

Only apps whose file location was confirmed from their source code or official
documentation are listed. `~` is your home folder; `<workspace>` is the folder
Aldwin was started in.

| App | Looks in | Format | Verified |
|---|---|---|---|
| Claude Code | `~/.claude.json` (user servers and the open workspace's servers), `<workspace>/.mcp.json` | JSON | 2026-11-01, docs |
| Claude Desktop | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`, Linux `~/.config/Claude/claude_desktop_config.json` | JSON | 2026-11-01, docs |
| Cline | `~/.cline/data/settings/cline_mcp_settings.json`, or the VS Code extension's `globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json` | JSON | 2026-11-01, source |
| Codex CLI | `~/.codex/config.toml`, `<workspace>/.codex/config.toml` | TOML | 2026-11-01, docs |
| GitHub Copilot CLI | `~/.copilot/mcp-config.json` | JSON | 2026-11-01, docs |
| Cursor | `~/.cursor/mcp.json`, `<workspace>/.cursor/mcp.json` | JSON | 2026-11-01, docs |
| Devin | `~/.config/devin/mcp_config.json`, `<workspace>/.devin/mcp_config.json` | JSON | 2026-11-01, docs |
| Gemini CLI | `~/.gemini/settings.json`, `<workspace>/.gemini/settings.json` | JSON | 2026-11-01, docs |
| Goose | `~/.config/goose/config.yaml` | YAML | 2026-11-01, source |
| Hermes Agent | `~/.hermes/config.yaml` | YAML | 2026-11-01, source |
| nanobot | `~/.nanobot/config.json` | JSON | 2026-11-01, source |
| opencode | `~/.config/opencode/opencode.json` or `.jsonc`, `<workspace>/opencode.json` | JSONC | 2026-11-01, docs |
| Pi | `~/.pi/agent/mcp.json`, `<workspace>/.pi/mcp.json` | JSON | 2026-11-01, source |
| VS Code | macOS `~/Library/Application Support/Code/User/mcp.json`, Linux `~/.config/Code/User/mcp.json`, `<workspace>/.vscode/mcp.json` | JSONC | 2026-11-01, source |
| Zed | `~/.config/zed/settings.json`, `<workspace>/.zed/settings.json` | JSONC | 2026-11-01, source |

Known gaps: Copilot CLI's bare project format (server names at the top level of
`.mcp.json` or `.github/mcp.json`) isn't detected, and Pi's `!command` values
are never run, only noted. Windows paths aren't searched.

### Your own files

Add any other file to `connectors.import_paths` in `~/.aldwin/config.yaml`:

```yaml
connectors:
  import_paths:
    - ~/work/team-mcp.toml
```

Aldwin works out the format from the file's extension and top-level keys.

### Wanted

These apps aren't listed because their file location couldn't be confirmed from
source or official docs. A contribution with evidence is welcome:
LM Studio, Windsurf (its docs now point to Devin, above), Kiro, Roo Code,
Continue, Amp.

## Add an app

Each app is one YAML file in
`src/aldwin/core/assets/connectors/import_sources/`. No Python is needed when
the app's file matches a format Aldwin already reads.

1. Find where the app stores its servers, from its source code or its official
   documentation. Third-party blog posts don't count: paths in them are often
   stale.
2. Copy the closest existing file and change `app`, `homepage`, `verified`,
   `verified_from`, `format`, `servers_at` and `locations`. Use `~` for the
   home folder and `{workspace}` for the open workspace.
3. Pick the `dialect`. `standard` covers the common `mcpServers` shape with
   `command`, `args`, `env`, `url` and `headers`. The others are `gemini`,
   `opencode`, `codex`, `zed` and `goose`. A shape none of these read needs a
   new mapper in `src/aldwin/core/connectors/import_dialects.py` and a test
   for it; open an issue first if you're unsure.
4. Add a fixture with fake values only to `tests/fixtures/import/`, named after
   your source file with `-` replaced by `_`, and add a line to the README
   there saying what you checked it against.
5. Add the app to the table above, in alphabetical order.
6. Run `uv run pytest tests/connectors/test_import_sources.py -q`. It checks
   that every source file loads, has a fixture that imports, and is listed
   here.

If an app moves its file, update `locations` and `verified` in its source file
and the row above. If a path is wrong on a platform you can check, the same
change fixes it.
