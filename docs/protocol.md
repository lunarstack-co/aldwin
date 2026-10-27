# Aldwin protocol (`v1`)

Aldwin separates its **brain** (`aldwin.core`) from its **doors** (`aldwin.cli`, and later desktop, browser extension, or Telegram) with a small, typed message contract in `aldwin.protocol`.

- **The one rule:** a door never imports `aldwin.core` or `agentling`. It talks to the host exclusively through `AldwinClient` and `aldwin.protocol`.
- **Transport:** in `0.0.1`, client and host exchange messages in-process over `InProcessTransport` (`asyncio.Queue` pair with JSON validation). From `0.1` onward, the daemon uses newline-delimited JSON over `~/.aldwin/aldwin.sock` with the exact same messages.

---

## Envelope

Every message is a Pydantic v2 model with `model_config = ConfigDict(extra="ignore")`:

| Field | Direction | Type | Description |
|---|---|---|---|
| `v` | Both | `int = 1` | Protocol version number. Bumped on any breaking change. |
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

### Events (host → door, `type = "event"`)

| `kind` | Model | Fields | Terminal? | Description |
|---|---|---|---|---|
| `text` | `TextEvent` | `delta: str` | No | Streamed assistant text chunk. |
| `tool_call` | `ToolCallEvent` | `call_id: str`, `name: str`, `args_preview: str` (≤ 120 chars) | No | Tool execution started. |
| `tool_result` | `ToolResultEvent` | `call_id: str`, `ok: bool`, `summary: str` (≤ 120 chars), `duration_ms: int` | No | Tool execution completed. |
| `approval` | `ApprovalEvent` | `approval_id: str`, `summary: str`, `detail: str = ""` | No | Host is paused waiting for a `yes` or `no` `approve` request. |
| `history` | `HistoryEvent` | `messages: list[HistoryItem]`, `interrupted: bool` | No | Past messages (`role: "user" \| "assistant" \| "tool"`, `text`, `at`). |
| `status` | `StatusEvent` | `message: str` | No | Non-terminal status notice (e.g. `OpenAI is busy, retrying (2 of 3)…`). |
| `done` | `DoneEvent` | `reason: "answered" \| "stopped" \| "max_steps" \| "ok"`, `usage: TokenUsage \| None`, `max_steps: int \| None` (set when `reason="max_steps"`) | **Yes** | Request completed normally. |
| `error` | `ErrorEvent` | `code: ErrorCode`, `message: str` | **Yes** | Request failed with a single-sentence actionable message. |

### Error codes (`ErrorCode`)

- `version_mismatch`: client's `v_min..v_max` does not include the host's `PROTOCOL_VERSION`.
- `protocol`: a request was sent before completing the `hello` handshake.
- `busy`: a `turn` or `set_model` was sent while another turn is already running.
- `unknown_approval`: `approve` referenced an `approval_id` that does not exist or already finished.
- `unknown_model`: `set_model` requested an unsupported or blank model name.
- `auth_failed`: OpenAI rejected the API key (`401` or `403`).
- `rate_limited`: OpenAI rate limit reached (`429`).
- `model_unavailable`: OpenAI connection, timeout, or `5xx` error.
- `internal`: unexpected error (stack trace logged to `~/.aldwin/logs/aldwin.log`, never sent in the event).

---

## Protocol rules

1. **Handshake first (`FR-4`):** The client must send `hello` before any other request. If `v_min <= 1 <= v_max`, the host replies `done(reason="ok")`; otherwise `error(code="version_mismatch")`. Any request before a successful `hello` receives `error(code="protocol")`.
2. **One terminal event per request (`FR-5`):** Every request receives zero or more non-terminal events followed by **exactly one** terminal event (`done` or `error`) carrying `re = request.id`. Enforced by `RequestScope` in `aldwin.host.runner`.
3. **Host asks, door answers (`FR-9`):** Tools never touch the terminal. They call `await ask(summary, detail)` injected by the host. The host creates an `asyncio.Future`, emits `approval(re=turn_id, approval_id=..., summary=..., detail=...)`, and waits for the door to send `approve(approval_id=..., answer="yes" | "no")`.
4. **Stop (`FR-8`):** On `stop`, the host resolves any pending approvals as `"no"` (`False`), calls `session.interrupt()`, cancels the turn task, flushes any partial streamed text into the session, saves atomically to disk, emits `done(re=turn_id, reason="stopped")`, and replies `done(re=stop_id, reason="ok")`.
5. **Forward compatibility (`FR-1`):** Unknown fields are ignored in both directions (`ConfigDict(extra="ignore")`).
