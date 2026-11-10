# Aldwin protocol (`v2`)

Aldwin separates its **brain** (`aldwin.core`) from its **doors** (`aldwin.cli`, and later desktop, browser extension, or Telegram) with a small, typed message contract in `aldwin.protocol`.

- **The one rule:** a door never imports `aldwin.core` or `agentling`. It talks to the host exclusively through `AldwinClient` and `aldwin.protocol`.
- **Transport:** in `0.0.1`, client and host exchange messages in-process over `InProcessTransport` (`asyncio.Queue` pair with JSON validation). From `0.1` onward, the daemon uses newline-delimited JSON over `~/.aldwin/aldwin.sock` with the exact same messages.
- **Host transport contract:** the host talks to every door through `HostTransport` (`aldwin.host.transport`). Two properties keep a dropped connection apart from a shutdown:
  - `closed` is permanent. It becomes true only when the host shuts the transport down, and only then does `receive_request()` return `None`. A transport must never return `None` because a peer disconnected: the host would stop serving and deny every pending approval.
  - `attached` reports whether a peer is connected right now. A door whose peer can drop and come back, such as a browser tab, sets `attached` to false on disconnect and back to true on reconnect, without closing the transport. Approvals already waiting stay open and can be answered after reconnecting, and the door may send `hello` again. A new approval asked while no peer is attached is denied with `no_approver`. For the in-process CLI transport, `attached` is simply "not closed".

---

## Envelope

Every message is a Pydantic v2 model with `model_config = ConfigDict(extra="ignore")`:

| Field | Direction | Type | Description |
|---|---|---|---|
| `v` | Both | `int = 2` | Protocol version number (`PROTOCOL_VERSION`). The host also speaks `v1` to older doors. |
| `type` | Both | `str` | Request discriminator (`hello`, `turn`, `stop`, `approve`, `history`, `set_model`) or `"event"` for all host events. |
| `id` | Door → Host | `str` | Unique request identifier (e.g. `req_a1b2c3d4e5f6`). |
| `re` | Host → Door | `str` | Request `id` that this event replies to. Required on every event. |
| `kind` | Host → Door | `str` | Event discriminator (`text`, `tool_call`, `tool_result`, `approval`, `history`, `status`, `done`, `error`). |
| `channel` | Both | `str = "cli"` | Originating door channel. |
| `sender` | Both | `str` | `"cli"` on requests; `"host"` on events. |

---

## Protocol messages

### Requests (door → host)

| `type` | Model | Fields | Description |
|---|---|---|---|
| `hello` | `HelloRequest` | `v_min: int = 1`, `v_max: int = 1` | Version handshake. Must be the first request on a connection. |
| `turn` | `TurnRequest` | `text: str` | Start a conversation turn with the user's message. |
| `stop` | `StopRequest` | *(none)* | Interrupt and cancel the active turn (`Esc`). |
| `approve` | `ApproveRequest` | `approval_id: str`, `answer: "yes" \| "no"` | Answer a pending `approval` event from the host. |
| `history` | `HistoryRequest` | `limit: int = 20` (`1..500`) | Fetch the last `limit` user/assistant messages and `interrupted` flag. |
| `set_model` | `SetModelRequest` | `name: str` | Switch the model for subsequent turns and persist in `config.yaml`. |
| `models.list` | `ModelsListRequest` | `provider: str \| None = None`, `refresh: bool = False` | List available models across configured and local providers. |
| `provider.key` | `ProviderKeyRequest` | `provider: str`, `key: str` | Validate and persist an API key for a cloud provider. |
| `provider.server` | `ProviderServerRequest` | `name: str`, `base_url: str`, `key: str \| None = None`, `title: str \| None = None`, `local: bool = False` | Register a custom OpenAI-compatible server in `config.yaml`. |

### Events (host → door, `type = "event"`)

| `kind` | Model | Fields | Terminal? | Description |
|---|---|---|---|---|
| `text` | `TextEvent` | `delta: str` | No | Streamed assistant text chunk. |
| `tool_call` | `ToolCallEvent` | `call_id: str`, `name: str`, `args_preview: str` (≤ 120 chars) | No | Tool execution started. |
| `tool_result` | `ToolResultEvent` | `call_id: str`, `ok: bool`, `summary: str` (≤ 120 chars), `duration_ms: int` | No | Tool execution completed. |
| `approval` | `ApprovalEvent` | `approval_id: str`, `summary: str`, `detail: str = ""` | No | Host is paused waiting for a `yes` or `no` `approve` request. |
| `history` | `HistoryEvent` | `messages: list[HistoryItem]`, `interrupted: bool` | No | Past messages (`role: "user" \| "assistant" \| "tool"`, `text`, `at`). |
| `status` | `StatusEvent` | `message: str`, `turn_streamed_chunks: int`, `active_tool: str \| None`, `partial_text: str \| None` | No | Status notice (e.g. `OpenAI is busy, retrying (2 of 3)…`) and the current turn's progress. See [Reconnecting](#reconnecting). |
| `models` | `ModelsEvent` | `current: str`, `providers: list[ProviderModels]` | No | List of providers and models returned in response to `models.list`. |
| `done` | `DoneEvent` | `reason: "answered" \| "stopped" \| "max_steps" \| "ok"`, `usage: TokenUsage \| None`, `max_steps: int \| None` (set when `reason="max_steps"`) | **Yes** | Request completed normally. |
| `error` | `ErrorEvent` | `code: ErrorCode`, `message: str` | **Yes** | Request failed with a single-sentence actionable message. |

### Error codes (`ErrorCode`)

- `version_mismatch`: client's `v_min..v_max` does not include the host's `PROTOCOL_VERSION`.
- `protocol`: a request was sent before completing the `hello` handshake.
- `busy`: a `turn`, `set_model`, `sessions.open` or `sessions.new` was sent while another turn is already running, or a `turn` was sent while another Aldwin window is answering in the same conversation.
- `unknown_approval`: `approve` referenced an `approval_id` that does not exist or already finished.
- `unknown_model`: `set_model` requested an unsupported or blank model name.
- `auth_failed`: the active model provider rejected the API key (`401` or `403`).
- `rate_limited`: the active model provider rate-limited the request (`429`).
- `model_unavailable`: connection, timeout, or `5xx` from the active model provider (cloud or local).
- `request_rejected`: the provider refused the request itself (`4xx` other than `401`, `404` and `429`), for example more than 128 tools switched on. The message says what to change.
- `internal`: unexpected error (stack trace logged to `~/.aldwin/logs/aldwin.log`, never sent in the event).

---

## Protocol rules

1. **Handshake first (`FR-4`):** The client must send `hello` before any other request. If `v_min <= 1 <= v_max`, the host replies `done(reason="ok")`; otherwise `error(code="version_mismatch")`. Any request before a successful `hello` receives `error(code="protocol")`.
2. **One terminal event per request (`FR-5`):** Every request receives zero or more non-terminal events followed by **exactly one** terminal event (`done` or `error`) carrying `re = request.id`. Enforced by `RequestScope` in `aldwin.host.runner`.
3. **Host asks, door answers (`FR-9`):** Tools never touch the terminal. They call `await ask(summary, detail)` injected by the host. The host creates an `asyncio.Future`, emits `approval(re=turn_id, approval_id=..., summary=..., detail=...)`, and waits for the door to send `approve(approval_id=..., answer="yes" | "no")`.
4. **Stop (`FR-8`):** On `stop`, the host resolves any pending approvals as `"no"` (`False`), calls `session.interrupt()`, cancels the turn task, flushes any partial streamed text into the session, saves atomically to disk, emits `done(re=turn_id, reason="stopped")`, and replies `done(re=stop_id, reason="ok")`.
5. **Forward compatibility (`FR-1`):** Unknown fields are ignored in both directions (`ConfigDict(extra="ignore")`).

The tables above list the original `v1` messages. `v2` also has the skills and todo messages (`skills.list`, `skills.show`, `todo.get`; `skills`, `skills.used`, `skill.body`, `todo.updated`, `notice`), defined in `aldwin/protocol/messages.py`.

---

## Conversations

Each conversation is one file in `~/.aldwin/sessions/`, and the host has exactly one active conversation at a time. These messages let any door list, open and start conversations. They were added without bumping `v` from 2.

### Requests

| `type` | Model | Fields | Description |
|---|---|---|---|
| `sessions.list` | `SessionsListRequest` | none | List saved conversations and the active one. |
| `sessions.open` | `SessionsOpenRequest` | `session_id: str` | Make a saved conversation the active one. |
| `sessions.new` | `SessionsNewRequest` | none | Start a conversation, reusing an empty one when there is one. |

### Event

| `kind` | Model | Fields | Description |
|---|---|---|---|
| `sessions` | `SessionsEvent` | `items: list[SessionMetaItem]`, `current: SessionMetaItem \| None` | Each item has `id`, `created_at`, `updated_at`, `model`, `title` and `interrupted`. |

### Rules

- `sessions.list` replies with `items`, newest first, and `current`. `items` leaves out conversations with no turns, so the active conversation may appear only in `current`. Unreadable files are skipped.
- `sessions.open` and `sessions.new` reply with `current` only and an empty `items`. They do not send the conversation; the door asks for `history` afterwards.
- `sessions.open` with an unknown id gets `error(code="not_found")`. Opening the active conversation succeeds and changes nothing.
- `sessions.new` reuses the active conversation, or else the newest saved one, while it has no turns. Only when both have turns does it create a new file, so repeated new chats never leave a trail of empty files.
- While a turn is running, `sessions.open` and `sessions.new` get `error(code="busy")` and the active conversation does not change. The terminal's `/new` is different: it stops the running turn first.
- A new conversation has an empty `title`. The title comes from the first message.
- Switching saves the conversation being left without changing its `updated_at`, so opening a conversation and leaving it does not move it up the list.

---

## Turns across windows

The terminal and the browser door run as separate processes on one home folder. While a turn runs, its host holds an exclusive lock on `~/.aldwin/state/locks/<session id>.lock`. The lock is taken when the turn starts and released when it ends with `done` or `error`, so an open window that is idle never blocks the other one. A `turn` in a conversation another window is answering gets `error(code="busy")`.

At the start of each turn, after taking the lock, the host checks whether another window saved the conversation since it last did. If so, it loads the saved file before running the turn, so the latest save always wins.

## Reconnecting

A door can drop and come back, for example when a browser tab reloads. If it drops during a turn it started attached, or while approvals are waiting, approvals stay open for 60 seconds. A door that reattaches and sends `hello` in that time can still answer them, and approvals asked meanwhile wait for it too. After 60 seconds, waiting approvals are denied as if no one were there to answer (`no_approver`), and the turn carries on without them.

Events sent while no door is attached are not kept. To catch up, a door that reattaches sends:

| `type` | Model | Fields | Description |
|---|---|---|---|
| `status` | `StatusRequest` | none | Reply with one `status` event, then `done`. Answered mid-turn. |

The `status` event's `turn_streamed_chunks` counts the text deltas the current turn has sent, `partial_text` is that text joined, and `active_tool` names the tool running now. All are zero or empty when no turn is running. The door then asks for `history` to show the conversation so far. There is no log of past events to replay.

---

## The browser's WebSocket

`aldwin web` carries these messages over one WebSocket at `/v1/ws`, one JSON request or event per text frame. The host stamps every request from it with `channel` and `sender` `web`, whatever the page sent, and every event to it with `channel` `web`.

1. The signed-in page asks `POST /v1/ws-ticket` and gets `{ticket, expires_in}`. This needs the session cookie and the same Host, Origin and `Sec-Fetch-Site` checks as other `/v1` routes. Until a host is serving, it answers 503 with `error: "host_not_ready"`.
2. It opens `/v1/ws?ticket=<ticket>` within 30 seconds. The upgrade must carry an `Origin` that is this server's own, and the ticket works once. A missing, reused or expired ticket is refused with 401, a foreign or missing Origin with 403, and a server with no host yet with 503, all before the socket opens.
3. It sends `hello` and carries on as any door.

Only one tab is live at a time. A second tab's socket is closed with code 4409 and reason `already_attached`. Opening with `&take_over=1` instead closes the first tab's socket with 4000 `replaced` and serves the new one. A tab that closes only detaches: approvals wait as described under [Reconnecting](#reconnecting). A message that is not a valid request gets `error(code="protocol")`; a binary frame closes the socket with 1003. Stopping `aldwin web` closes the socket with 1001.

---

## REST helpers in `aldwin web`

The browser door also reads and writes a few documents over plain HTTP. These routes are helpers, not a second protocol: starting, opening and running a conversation, approvals, skills and models stay on the messages above. Every route needs the session cookie and the same Host, Origin and `Sec-Fetch-Site` checks as the rest of `/v1` (see [privacy](privacy.md#the-browser-window)); a signed-out request gets 401 and a request from another page gets 403.

| Route | Does |
|---|---|
| `GET /v1/health`, `GET /v1/home` | Version and the Aldwin home folder. |
| `GET /v1/conversations` | Saved conversations newest first (`id`, `created_at`, `updated_at`, `model`, `title`, `interrupted`). Conversations with no turns are left out. |
| `GET /v1/conversations/{id}` | The conversation file exactly as saved in `sessions/`, or 404. |
| `GET /v1/settings` | `model`, `check_for_updates`, `limits`, `search` and every key masked with its source. |
| `PATCH /v1/settings` | Changes `model`, `check_for_updates`, `limits.*` or `search.provider`/`search.searxng_url`. Any other field, keys included, is a 400 and nothing is written. |
| `POST /v1/settings/secrets` | `{kind: "provider" \| "search", name, key, base_url?}`. Checks the key with the provider, saves it only if the check passes, and answers with the key masked. Saving a search key also makes that provider the one `web_search` uses, as `/search` does. A rejected key is a 400; a provider that cannot be reached is a 502. |
| `GET /v1/setup/status` | Which first-run steps are done: keys (masked), model, and whether the profile step was answered or skipped. |
| `POST /v1/setup/check-key` | Same body as `secrets`; checks the key without saving it. |
| `POST /v1/setup/provider-key` | `{name, key, base_url?}`; checks and saves a model provider key. |
| `POST /v1/setup/detect-local` | Local model runtimes (Ollama, LM Studio) and their models. |
| `POST /v1/setup/models` | `{provider}`; the models that provider offers. |
| `POST /v1/setup/save` | `{openai_api_key?, tavily_api_key?, model?}`; checks every key first and saves nothing if one fails. |
| `POST /v1/setup/profile` | `{first_name?, occupation?, personality?, skipped?}`; writes `state/onboarding.json`. |
| `POST /v1/ws-ticket` | A one-time ticket for the WebSocket; see above. |

A malformed body gets 422 with the field and the reason, never the submitted values. These routes never start an agent, so they work before any model key exists.

---

## Connectors

Connectors add MCP servers whose tools join the conversation: remote servers by URL, and local servers started from a command on this computer (macOS and Linux only). These messages were added without bumping `v` from 2: door and host ship in one package over the in-process transport, so they always agree. Before the daemon ships, doors must tolerate unknown event `kind`s.

### Requests

| `type` | Fields | Answer |
|---|---|---|
| `connectors.list` | *(none)* | `connectors`, then `done` |
| `connectors.add` | `url` or `command` (exactly one; `command` is a local command line, run without a shell), `env_allow: list[str]` and `cwd` (an absolute folder; default the workspace) for a local command, `name: str \| None` (suggested from the host name or the command when omitted), `title`, `auth: "none" \| "bearer" \| "oauth"`, `token` (never in `repr`), `token_env`, `dry_run: bool = False`, `client_id` (a pre-registered OAuth client), `no_browser: bool = False` | Runs in the background. `dry_run`: `connector.probe`, then `done` (refused for `oauth`). Otherwise `connector.status`, then `done`. `oauth`, or `bearer` with no token, first sends `connector.auth` and waits for the user. A local `command` first sends `approval` (`tool="connectors.add"`, never grantable) and runs nothing unless the answer is `yes`; its `dry_run` only checks the command and suggests a name, without starting or asking. Nothing is saved on failure. |
| `connectors.import` | `paths: list[str] = []`, `dry_run: bool = False`, `rename_conflicts: bool = False` | `connectors.import`, then `done`. `dry_run` only lists the files other agent apps keep MCP servers in. Otherwise `paths` are imported, or every readable file found when `paths` is empty. Nothing is started: imported local commands, and everything from a project file, are saved disabled. One unreadable file is a warning, not a failure. |
| `connectors.remove` | `name` | `done` (the saved token is deleted too) |
| `connectors.enable` | `name` | Runs in the background: `connector.status`, then `done` |
| `connectors.disable` | `name` | `connector.status`, then `done`; the tools leave the conversation at once |
| `connectors.reconnect` | `name` | Runs in the background: `done`; the new status follows on the host stream |
| `connectors.tool_policy` | `name`, `tool` (the server's tool name), `setting: "auto" \| "ask" \| "off" \| "default"` | `connectors`, then `done` |
| `connectors.trust_hints` | `name`, `trust: bool` | `connectors`, then `done` |
| `connectors.login` | `name`, `no_browser: bool = False` | Runs in the background: `connector.auth`, then `connector.status`, then `done` |
| `connectors.logout` | `name` | `connector.status` (`needs_auth`), then `done`. Deletes the local sign-in; nothing is revoked at the server. |
| `connectors.auth_paste` | `name`, `redirect_url` (never in `repr`) | `done`, or `error(code="connector")` for an address from another sign-in or with no code; the sign-in keeps waiting |
| `connectors.auth_token` | `name`, `token` (never in `repr`) | `done`; the waiting `connectors.add` carries on with the token |

`connectors.add`, `connectors.enable`, `connectors.reconnect` and `connectors.login` run as owned tasks, so `approve`, `stop` and the `auth_*` answers are still served while they wait. Each still gets exactly one `done` or `error`. A second flow for a name already in progress gets `error(code="busy")`. `stop` cancels a flow that is waiting for the user; that flow ends with `done(reason="stopped")` and saves nothing.

### Events

| `kind` | Fields |
|---|---|
| `connectors` | `items: list[ConnectorInfo]`, `error: str \| None` (the `connectors.yaml` problem, when the file is invalid), `catalog: list[CatalogItem]` |
| `connectors.import` | `found: list[ImportSourceInfo]` (`app`, `scope: "user" \| "project"`, `path`, `servers`, `error`), `rows: list[ImportRowInfo]` (`name`, `kind`, `status: "imported" \| "skipped" \| "failed"`, `detail`, `app`), `imported`, `values` (secret values moved to the credential store), `warnings: list[str]` |
| `connector.status` | `name`, `status: "connecting" \| "connected" \| "failed" \| "disabled" \| "needs_auth"`, `tools: int`, `error`, `retry_in_s` |
| `connector.auth` | `name`, `title`, `mode: "browser" \| "paste" \| "token"`, `url` (the sign-in address; `null` for `token`), `token_help_url`, `expires_in` (seconds left to answer). Also sent during a `turn`, on the turn's `re`, when `propose_connector` needs the user to sign in; the door answers it the same way (`connectors.auth_paste`, `connectors.auth_token`, or `stop` to give up, which ends the turn) |
| `connector.probe` | `name`, `title`, `tools: list[ConnectorToolInfo]`, `read: int`, `write: int` |

`ConnectorInfo` has `name`, `title`, `url` (`null` for a local connector), `enabled`, `status`, `auth`, `trust_read_only_hints`, `tools`, `error`, `retry_in_s`, `transport` (`http`, `stdio` or `sse`), `command` (how a local connector starts, for display) and `note`. An `sse` entry is never connected (`error: "SSE is not supported"`); a local entry whose command changed since it was approved shows `error: "needs approval: open /connectors to enable it"` and does not start. `connectors.enable` on such an entry sends `approval` (`tool="connectors.enable"`) first. `ConnectorToolInfo` has `name`, `model_name` (`mcp__<connector>__<tool>`), `title`, `description`, `read_only`, `setting` (the user's choice, or `null`) and `effective` (`auto`, `ask` or `off`).

`CatalogItem` has `name`, `title`, `purpose`, `url`, `auth`, `docs_url`, `keywords`, `token_help_url` and `added` (already in `connectors.yaml`).

### Sign-in

For `mode = "browser"` the door prints `url` and opens it. Aldwin listens on `127.0.0.1` (port 47823, then 47824, then 47825) for the browser's return. For `mode = "paste"` (or when the browser can't come back) the door asks for the address the browser ended on and sends `connectors.auth_paste`. For `mode = "token"` the door reads a token without echo and sends `connectors.auth_token`. The user has `expires_in` seconds (five minutes) to finish.

A connector whose sign-in has expired or been revoked moves to `needs_auth` without opening a browser. Its tools stay listed and answer `<Title> needs you to sign in again. Ask the user to run /connectors.` The next turn carries one `notice`: `<Title> needs you to sign in again: /connectors.`

`tool_call` and `approval` gained optional `connector`, `connector_title`, `remote_tool` and `reason` (for example `server says read-only`). `GrantScope` gained `"tool"`: "Yes for this session" for one connector tool.

### Host events

Events not tied to a request, such as `connector.status` and a `notice` when a connector drops (`GitHub disconnected; retrying.`) or comes back (`GitHub reconnected.`), use `re = "host"` (`HOST_EVENT_RE`). While a turn is running they use the turn's `re` instead. Read them with `AldwinClient.host_events()`.

### Error codes

- `connector`: a user error, with the message to show as is (`Connector URL must use https:// (got <url>)`, `A connector named <name> already exists`, `That address isn't an MCP server (got text/html).`, `Couldn't reach <host>: <reason>.`, `The server refused the token (401).`).
- `auth_required`: the server answered 401 and no token was given (`<host> needs a token.`). Ask for a token and retry `connectors.add` with it. Also sent when a sign-in is needed and no one can answer it.
