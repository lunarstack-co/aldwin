# Safety

Web pages, documents, and files can contain prompt-injection text, and Aldwin runs on your machine without an OS sandbox. Aldwin defends against this with a single, deterministic policy engine that checks every tool call before it runs. The model can't switch it off, and no answer, grant, or config key can override its hard floor.

To report a security problem, see [SECURITY.md](../SECURITY.md).

## Approval tiers

Every tool has one of three tiers.

- **Auto:** read-only tools inside the active workspace (`read_file`, `read_document`, `list_dir`, `glob`, `grep`), `web_search`, `web_fetch`, `recall`, new `remember` notes, `todo`, and `calculate`.
- **Diff:** file and state changes (`edit_file`, `write_file`, `remember` with `replace`, `forget`, `create_skill`, `set_persona`) show a coloured unified diff (folded at 24 lines, `Ctrl+O` to expand) with **No** as the default.
  - Inside the workspace, you can allow edits to a single file for the rest of the session (`a`).
  - Build files (`pyproject.toml`, `Makefile`, `Dockerfile`, `.github/workflows/*.yml`, and similar) always ask.
- **Ask:** `run_command` and `download_file` always ask (`y/n`) and can never be allowed for the session.

Outside the workspace, reads ask once per folder per session, and writes ask every time.

## Hard floor

These are refused before Aldwin even asks you.

**Blocked secret paths.** Aldwin never reads or writes:

- `~/.ssh/**`, `~/.aws/**`, `~/.gnupg/**`, `~/.kube/**`, `~/.azure/**`
- `~/.config/gh/**`, `~/.config/gcloud/**`, `~/.docker/config.json`
- `**/*.pem`, `**/*.key`, `**/*.p12`, `**/*.pfx`
- `**/id_rsa*`, `**/id_ed25519*`, `**/id_ecdsa*`
- `**/.netrc`, `**/.pgpass`, `**/.npmrc`, `**/.pypirc`, `**/.git-credentials`
- `**/.env`, `**/.env.*`, `**/.envrc` (except `.env.example`, `.env.sample`, `.env.template`)
- `~/.aldwin/**`, except `~/.aldwin/tmp/**`, which `read_file` can read to page through long tool output

Folder listings and searches hide these files and say how many were hidden.

**Read-only `.git`.** Writes to `.git/**` are always refused, so file tools can't change git hooks or config.

**Refused commands.** `run_command` refuses, before asking:

- privilege escalation (`sudo`, `su`, `doas`)
- interactive programs and bare REPLs
- destructive commands: `rm -rf` on `/`, `~` or system folders, `mkfs`, `dd` or redirects to block devices, fork bombs, `kill -9 -1`, `shutdown`, `reboot`, `halt`, `poweroff`, `init 0|6`, `systemctl poweroff`
- any command that names a blocked secret path

## Clean command environment

`run_command` starts from an empty environment and copies only safe variables (`PATH`, `HOME`, `USER`, `LOGNAME`, `SHELL`, `LANG`, `LC_*`, `TERM`, `TMPDIR`, `TZ`, `PWD`), plus non-secret names you list in `run_command.env_allow` in `config.yaml`.

Any `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, or `*_CREDENTIAL` variable is always stripped, even if you list it in `env_allow`.

Commands also show yellow risk labels when they do something worth a second look, such as `⚠ pipes into a shell`, `network download`, or `recursive delete`.

## Approvals match what you saw

A Diff approval is tied to a `sha256` digest of the tool, its resolved arguments, and the diff. If the file changed after you saw the diff, Aldwin asks again with the new diff instead of applying a stale approval.

If you decline three actions in one turn, Aldwin stops the turn.

## Tool output is untrusted

Every tool result (except `todo` and `calculate`) is wrapped in `<untrusted id="u-…" source="…">` tags with any inner tags escaped. Text read from a file, page, or command output is treated as data, never as an instruction.

## Approval log

Every policy decision (`auto`, `asked`, `blocked`) writes one JSON line to `~/.aldwin/logs/approvals.jsonl`:

`ts`, `session`, `call_id`, `tool`, `target`, `digest`, `tier`, `decision`, `answer`, `source`, `reason`

The log rotates at 10 MB, and secret-shaped tokens are replaced with `‹redacted›`. File contents, diffs, and tool outputs are never logged.

## Known limits

- **Read, then send to the web.** Workspace reads and `web_fetch` both run without asking, so a prompt-injected document could try to send non-blocked workspace text out through a fetched URL. Secret files are blocked, all tool output is marked untrusted, and every `web_fetch` URL is recorded in the approval log.
- **Hard links.** A hard link inside the workspace that points to a non-blocked file outside the workspace is not detected.
- **Shell inspection is best-effort.** Shell commands can't be fully parsed ahead of time. The main control is that every command needs your `y/n`, with risk labels shown.
