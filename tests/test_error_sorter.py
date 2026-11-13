"""Tests for error classification, provider-aware copy, and retry status lines."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentling import (
    Agent,
    Delta,
    ModelAuthError,
    ModelBadRequestError,
    ModelConnectionError,
    ModelContextLengthError,
    ModelError,
    ModelOutputTruncatedError,
    ModelRateLimitError,
    ModelServerError,
    ModelTimeoutError,
    RetryPolicy,
    Usage,
)
from agentling.memory import FinalStep, TaskStep
from agentling.testing import ScriptedModel
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.home import ensure_home
from aldwin.core.logging import _redact_text
from aldwin.core.models import (
    classify_model_error,
    is_local_model,
    parse_model_ref_parts,
    resolve_provider_title,
    retry_status_message,
)
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import (
    DoneEvent,
    ErrorEvent,
    NoticeEvent,
    StatusEvent,
    TextEvent,
)


async def _collect_turn(client: AldwinClient, text: str) -> list[Any]:
    events: list[Any] = []
    async for event in client.turn(text):
        events.append(event)
    return events


# Model references and error classification


def test_parse_model_ref_parts_handles_bare_prefixed_and_nested_refs() -> None:
    """Model reference parsing handles bare, prefixed, and multi-slash refs."""
    assert parse_model_ref_parts("gpt-4o") == ("openai", "gpt-4o")
    assert parse_model_ref_parts("anthropic/claude-3-5-sonnet") == (
        "anthropic",
        "claude-3-5-sonnet",
    )
    assert parse_model_ref_parts("ollama/qwen3:8b") == ("ollama", "qwen3:8b")
    assert parse_model_ref_parts("openrouter/meta-llama/llama-3-70b") == (
        "openrouter",
        "meta-llama/llama-3-70b",
    )
    assert parse_model_ref_parts("") == ("openai", "gpt-4o")
    assert parse_model_ref_parts(None) == ("openai", "gpt-4o")


def test_resolve_provider_title_maps_first_ship_and_fallbacks() -> None:
    """Provider title resolution maps known providers and custom fallbacks."""
    assert resolve_provider_title("openai") == "OpenAI"
    assert resolve_provider_title("anthropic") == "Anthropic"
    assert resolve_provider_title("gemini") == "Gemini"
    assert resolve_provider_title("deepseek") == "DeepSeek"
    assert resolve_provider_title("ollama") == "Ollama"
    assert resolve_provider_title("lmstudio") == "LM Studio"
    assert resolve_provider_title("openrouter") == "OpenRouter"
    assert resolve_provider_title("custom") == "Custom"


def test_classify_auth_error_names_provider_and_next_step() -> None:
    """Authentication errors produce auth_failed code and setup instructions."""
    code, msg = classify_model_error(
        ModelAuthError("bad key"), provider_title="Anthropic"
    )
    assert code == "auth_failed"
    assert msg == "Your Anthropic key was rejected. Run `aldwin setup` to replace it."

    code, msg = classify_model_error(ModelAuthError("bad key"), provider_title="OpenAI")
    assert code == "auth_failed"
    assert msg == "Your OpenAI key was rejected. Run `aldwin setup` to replace it."

    code, msg = classify_model_error(ModelAuthError("bad key"), provider_title="Gemini")
    assert code == "auth_failed"
    assert msg == "Your Gemini key was rejected. Run `aldwin setup` to replace it."


def test_classify_rate_limit_final_error_names_provider() -> None:
    """Exhausted rate limits produce rate_limited code and provider copy."""
    code, msg = classify_model_error(
        ModelRateLimitError("429"), provider_title="OpenAI"
    )
    assert code == "rate_limited"
    assert msg == "OpenAI rate limit reached. Wait a moment and try again."

    code, msg = classify_model_error(
        ModelRateLimitError("429"), provider_title="DeepSeek"
    )
    assert code == "rate_limited"
    assert msg == "DeepSeek rate limit reached. Wait a moment and try again."


def test_classify_server_error_final_error_names_provider() -> None:
    """Server errors produce rate_limited code and server error copy."""
    code, msg = classify_model_error(ModelServerError("500"), provider_title="Gemini")
    assert code == "rate_limited"
    assert msg == "Gemini returned a server error. Wait a moment and try again."

    code, msg = classify_model_error(ModelServerError("503"), provider_title="OpenAI")
    assert code == "rate_limited"
    assert msg == "OpenAI returned a server error. Wait a moment and try again."


def test_classify_cloud_unreachable_suggests_local_switch() -> None:
    """Cloud connection errors produce model_unavailable and local switch copy."""
    code, msg = classify_model_error(
        ModelConnectionError("refused"), provider_title="DeepSeek", local=False
    )
    assert code == "model_unavailable"
    assert msg == (
        "Can't reach DeepSeek. Check your connection, "
        "or switch to a local model with `/model`."
    )

    code, msg = classify_model_error(
        ModelTimeoutError("timed out"), provider_title="OpenAI", local=False
    )
    assert code == "model_unavailable"
    assert msg == (
        "Can't reach OpenAI. Check your connection, "
        "or switch to a local model with `/model`."
    )


def test_classify_local_closed_connection_instructs_restart() -> None:
    """Local connection errors produce model_unavailable and running copy."""
    code, msg = classify_model_error(
        ModelConnectionError("refused"), provider_title="Ollama", local=True
    )
    assert code == "model_unavailable"
    assert msg == "Ollama isn't running. Start it and try again."

    err = ModelConnectionError("refused")
    setattr(err, "local", True)
    code, msg = classify_model_error(err, provider_title="LM Studio")
    assert code == "model_unavailable"
    assert msg == "LM Studio isn't running. Start it and try again."


def test_classify_missing_model_local_uses_installed_in_copy() -> None:
    """Missing model on local daemon (404) uses installed-in copy."""
    code, msg = classify_model_error(
        ModelBadRequestError("model 'qwen3:14b' not found", status=404),
        model="ollama/qwen3:14b",
        provider_title="Ollama",
        local=True,
    )
    assert code == "unknown_model"
    assert (
        msg
        == "The model qwen3:14b isn't installed in Ollama. Pick another with `/model`."
    )


def test_classify_missing_model_cloud_uses_available_to_key_copy() -> None:
    """Missing model on cloud provider (404) uses available-to-key copy."""
    code, msg = classify_model_error(
        ModelBadRequestError("model not found", status=404),
        model="anthropic/claude-unknown",
        provider_title="Anthropic",
        local=False,
    )
    assert code == "unknown_model"
    assert (
        msg == "Model claude-unknown isn't available to your key. "
        "Pick another with `/model`."
    )


def test_classify_missing_model_without_explicit_model_uses_generic_subject() -> None:
    """Missing model when model is None or empty uses clean generic fallback."""
    code, msg = classify_model_error(
        ModelBadRequestError("model not found", status=404),
        model=None,
        provider_title="OpenAI",
        local=False,
    )
    assert code == "unknown_model"
    assert msg == "That model isn't available to your key. Pick another with `/model`."

    code, msg = classify_model_error(
        ModelBadRequestError("model not found", status=404),
        model="",
        provider_title="Ollama",
        local=True,
    )
    assert code == "unknown_model"
    assert msg == "The model isn't installed in Ollama. Pick another with `/model`."


def test_classify_context_length_overflow_suggests_new_conversation() -> None:
    """Context length error produces request_rejected with fresh session copy."""
    code, msg = classify_model_error(
        ModelContextLengthError("context overflow"), model="ollama/llama3.1:8b"
    )
    assert code == "request_rejected"
    assert (
        msg
        == "This conversation is too long for llama3.1:8b. Start a fresh one with /new."
    )


def test_classify_output_truncated_emits_length_limit_message() -> None:
    """Output truncation produces model length limit message."""
    code, msg = classify_model_error(ModelOutputTruncatedError("truncated"))
    assert code == "request_rejected"
    assert msg == "The answer was cut off because it reached the model's length limit."


def test_retry_status_for_rate_limit_formats_countdown() -> None:
    """Rate limit status line formats provider name and countdown."""
    msg = retry_status_message(
        ModelRateLimitError("busy"),
        attempt=2,
        max_retries=3,
        delay_s=4.0,
        provider_title="OpenAI",
    )
    assert msg == "OpenAI is busy, retrying in 4s (2/3)…"


def test_retry_status_for_server_error_formats_server_notice() -> None:
    """Server error status line formats provider and countdown."""
    msg = retry_status_message(
        ModelServerError("503"),
        attempt=1,
        max_retries=3,
        delay_s=1.0,
        provider_title="Gemini",
    )
    assert msg == "Gemini returned a server error, retrying in 1s (1/3)…"


def test_retry_status_rate_limit_with_fifty_in_message_stays_busy() -> None:
    """Rate limit exceptions mentioning numbers like 50 do not trigger server error."""
    exc = ModelRateLimitError("HTTP 429 after 50 requests in 1 minute")
    msg = retry_status_message(
        exc,
        attempt=1,
        max_retries=3,
        delay_s=2.0,
        provider_title="OpenAI",
    )
    assert msg == "OpenAI is busy, retrying in 2s (1/3)…"
    assert "server error" not in msg


def test_retry_status_for_cloud_connection_formats_reach_notice() -> None:
    """Transient cloud connection status line formats provider and countdown."""
    msg = retry_status_message(
        ModelConnectionError("dropped"),
        attempt=1,
        max_retries=3,
        delay_s=2.0,
        provider_title="DeepSeek",
    )
    assert msg == "Can't reach DeepSeek. Retrying in 2s (1/3)…"


def test_sensitive_keys_are_redacted_from_error_messages() -> None:
    """Secret API keys are redacted by _redact_text and stripped from messages."""
    canary = "sk-ant-api03-secret1234567890"
    redacted = _redact_text(f"Failed with key {canary}")
    assert canary not in redacted

    code, msg = classify_model_error(
        ModelAuthError(f"Invalid key {canary}"), provider_title="Anthropic"
    )
    assert code == "auth_failed"
    assert canary not in msg


def test_tool_count_limit_error_regression() -> None:
    """OpenAI 400 with tool limit array length preserves disable guidance."""
    exc = ModelBadRequestError(
        "'tools': maximum length 128, but got an array with length 161"
    )
    code, msg = classify_model_error(exc)
    assert code == "request_rejected"
    assert msg == (
        "OpenAI allows at most 128 tools and 161 are switched on. "
        "Turn some off in /connectors."
    )


def test_classify_model_error_fallback_branches() -> None:
    """Generic ModelError text-matching and bad request fallbacks work reliably."""
    code, msg = classify_model_error(
        ModelBadRequestError("bad parameter", status=400), provider_title="OpenAI"
    )
    assert code == "request_rejected"
    assert "OpenAI refused the request (HTTP 400)" in msg

    code, msg = classify_model_error(
        ModelBadRequestError("bad parameter", status=None), provider_title="OpenAI"
    )
    assert code == "request_rejected"
    assert "OpenAI refused the request (error)" in msg

    code, msg = classify_model_error(
        ModelError("401 key rejected"), provider_title="Anthropic"
    )
    assert code == "auth_failed"
    assert "Your Anthropic key was rejected" in msg

    code, msg = classify_model_error(
        ModelError("429 too many calls"), provider_title="OpenAI"
    )
    assert code == "rate_limited"
    assert "OpenAI rate limit reached" in msg

    code, msg = classify_model_error(
        ModelError("502 bad gateway"), provider_title="Gemini"
    )
    assert code == "rate_limited"
    assert "Gemini returned a server error" in msg

    code, msg = classify_model_error(
        ModelError("404 model gone"),
        model="ollama/qwen:7b",
        provider_title="Ollama",
        local=True,
    )
    assert code == "unknown_model"
    assert "The model qwen:7b isn't installed in Ollama" in msg

    code, msg = classify_model_error(
        ModelError("404 not found"),
        model="anthropic/ghost",
        provider_title="Anthropic",
        local=False,
    )
    assert code == "unknown_model"
    assert "Model ghost isn't available to your key" in msg

    code, msg = classify_model_error(
        ModelError("some unexpected local error"),
        provider_title="Ollama",
        local=True,
    )
    assert code == "model_unavailable"
    assert "Ollama isn't running" in msg

    code, msg = classify_model_error(
        ModelError("some generic cloud failure"),
        provider_title="DeepSeek",
        local=False,
    )
    assert code == "model_unavailable"
    assert "Can't reach DeepSeek" in msg


def test_is_local_model_and_retry_status_helpers() -> None:
    """Local model detection and retry status model resolution function properly."""
    assert is_local_model("ollama/qwen3:8b") is True
    assert is_local_model("lmstudio/llama3") is True
    assert is_local_model("openai/gpt-4o") is False
    assert is_local_model("custom/local-llm", local=True) is True

    msg = retry_status_message(
        ModelServerError("500"),
        attempt=1,
        max_retries=3,
        delay_s=1.0,
        model="gemini/gemini-2.5-pro",
    )
    assert "Gemini returned a server error" in msg

    msg_conn = retry_status_message(
        ModelConnectionError("refused"),
        attempt=2,
        max_retries=3,
        delay_s=3.0,
        model="deepseek/deepseek-chat",
    )
    assert "Can't reach DeepSeek" in msg_conn


# Host runner lifecycle and error events


async def test_host_auth_failure_turn(aldwin_home: Path) -> None:
    """Auth error emits single auth_failed ErrorEvent and zero retries."""
    ensure_home(aldwin_home)
    fake_model = FakeModel(turns=[FakeTurn.error(ModelAuthError("unauthorized"))])
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
        initial_model="anthropic/claude-3-5-sonnet",
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "hello")
        assert len(events) == 1
        err = events[0]
        assert isinstance(err, ErrorEvent)
        assert err.code == "auth_failed"
        assert (
            err.message
            == "Your Anthropic key was rejected. Run `aldwin setup` to replace it."
        )
        assert len(fake_model.calls) == 1
    finally:
        await client.close()
        await host.stop_host()


async def test_host_rate_limit_retry_then_succeed(aldwin_home: Path) -> None:
    """Transient rate limit emits StatusEvent before succeeding."""
    ensure_home(aldwin_home)
    fake_model = FakeModel(
        turns=[
            FakeTurn.error(ModelRateLimitError("busy", retryable=True)),
            FakeTurn.text("Hello"),
        ]
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(
            model=fake_model,
            retry=RetryPolicy(max_retries=3, base_delay=0.01, jitter=0.0),
        ),
        initial_model="openai/gpt-4o",
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "hi")
        statuses = [ev.message for ev in events if isinstance(ev, StatusEvent)]
        texts = [ev.delta for ev in events if isinstance(ev, TextEvent)]
        assert len(statuses) == 1
        assert "OpenAI is busy, retrying in" in statuses[0]
        assert "".join(texts) == "Hello"
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].reason == "answered"
        assert len(fake_model.calls) == 2
    finally:
        await client.close()
        await host.stop_host()


async def test_host_rate_limit_retry_exhaustion(aldwin_home: Path) -> None:
    """Exhausted retries emit StatusEvents followed by rate_limited error."""
    ensure_home(aldwin_home)
    fake_model = FakeModel(
        turns=[
            FakeTurn.error(ModelRateLimitError("busy", retryable=True)),
            FakeTurn.error(ModelRateLimitError("busy", retryable=True)),
            FakeTurn.error(ModelRateLimitError("busy", retryable=True)),
            FakeTurn.error(ModelRateLimitError("busy", retryable=True)),
        ]
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(
            model=fake_model,
            retry=RetryPolicy(max_retries=3, base_delay=0.01, jitter=0.0),
        ),
        initial_model="openai/gpt-4o",
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "hi")
        statuses = [ev.message for ev in events if isinstance(ev, StatusEvent)]
        assert len(statuses) == 3
        assert isinstance(events[-1], ErrorEvent)
        assert events[-1].code == "rate_limited"
        assert (
            events[-1].message
            == "OpenAI rate limit reached. Wait a moment and try again."
        )
    finally:
        await client.close()
        await host.stop_host()


async def test_host_local_closed_fail_fast(aldwin_home: Path) -> None:
    """Local closed daemon fails immediately with zero retries."""
    ensure_home(aldwin_home)
    conn_err = ModelConnectionError("Connection refused", retryable=False)
    setattr(conn_err, "local", True)

    fake_model = FakeModel(turns=[FakeTurn.error(conn_err)])
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(
            model=fake_model,
            retry=RetryPolicy(max_retries=3, base_delay=0.01, jitter=0.0),
        ),
        initial_model="ollama/qwen3:8b",
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "test")
        statuses = [ev for ev in events if isinstance(ev, StatusEvent)]
        assert len(statuses) == 0
        assert len(events) == 1
        err = events[0]
        assert isinstance(err, ErrorEvent)
        assert err.code == "model_unavailable"
        assert err.message == "Ollama isn't running. Start it and try again."
        assert len(fake_model.calls) == 1
    finally:
        await client.close()
        await host.stop_host()


async def test_host_cloud_unreachable_after_retries(aldwin_home: Path) -> None:
    """Cloud unreachable retries then suggests switching to local model."""
    ensure_home(aldwin_home)
    fake_model = FakeModel(
        turns=[
            FakeTurn.error(ModelConnectionError("network down", retryable=True)),
            FakeTurn.error(ModelConnectionError("network down", retryable=True)),
            FakeTurn.error(ModelConnectionError("network down", retryable=True)),
            FakeTurn.error(ModelConnectionError("network down", retryable=True)),
        ]
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(
            model=fake_model,
            retry=RetryPolicy(max_retries=3, base_delay=0.01, jitter=0.0),
        ),
        initial_model="deepseek/deepseek-chat",
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "test")
        statuses = [ev.message for ev in events if isinstance(ev, StatusEvent)]
        assert len(statuses) == 3
        assert "Can't reach DeepSeek. Retrying in" in statuses[0]
        assert isinstance(events[-1], ErrorEvent)
        assert events[-1].code == "model_unavailable"
        assert events[-1].message == (
            "Can't reach DeepSeek. Check your connection, "
            "or switch to a local model with `/model`."
        )
    finally:
        await client.close()
        await host.stop_host()


async def test_host_missing_local_model_404(aldwin_home: Path) -> None:
    """Local model 404 immediately reports model not installed."""
    ensure_home(aldwin_home)
    bad_req = ModelBadRequestError("model 'qwen3:14b' not found", status=404)
    fake_model = FakeModel(turns=[FakeTurn.error(bad_req)])
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
        initial_model="ollama/qwen3:14b",
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "hi")
        assert len(events) == 1
        err = events[0]
        assert isinstance(err, ErrorEvent)
        assert err.code == "unknown_model"
        assert err.message == (
            "The model qwen3:14b isn't installed in Ollama. Pick another with `/model`."
        )
        assert len(fake_model.calls) == 1
    finally:
        await client.close()
        await host.stop_host()


async def test_host_context_trim_recovery(aldwin_home: Path) -> None:
    """ModelContextLengthError trims older history, clears step calls, and recovers."""
    ensure_home(aldwin_home)
    fake_model = FakeModel(
        turns=[
            FakeTurn.error(ModelContextLengthError("too long")),
            FakeTurn.text("Recovered"),
        ]
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
        initial_model="ollama/llama3.1:8b",
    )
    host._agent_session.memory.add(TaskStep(task="Old question 1"))
    host._agent_session.memory.add(FinalStep(answer="Old answer 1"))
    host._agent_session.memory.add(TaskStep(task="Old question 2"))
    host._agent_session.memory.add(FinalStep(answer="Old answer 2"))

    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "new question")
        notices = [ev.message for ev in events if isinstance(ev, NoticeEvent)]
        texts = [ev.delta for ev in events if isinstance(ev, TextEvent)]
        assert notices == [
            "This conversation is too long for llama3.1:8b. "
            "Aldwin trimmed older messages."
        ]
        assert "".join(texts) == "Recovered"
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].reason == "answered"
    finally:
        await client.close()
        await host.stop_host()


async def test_host_context_overflow_unrecoverable(aldwin_home: Path) -> None:
    """ModelContextLengthError with single exchange cannot trim and fails."""
    ensure_home(aldwin_home)
    fake_model = FakeModel(turns=[FakeTurn.error(ModelContextLengthError("too long"))])
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
        initial_model="ollama/llama3.1:8b",
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "massive prompt")
        assert len(events) == 1
        err = events[0]
        assert isinstance(err, ErrorEvent)
        assert err.code == "request_rejected"
        assert err.message == (
            "This conversation is too long for llama3.1:8b. "
            "Start a fresh one with /new."
        )
    finally:
        await client.close()
        await host.stop_host()


async def test_host_output_cutoff_notice(aldwin_home: Path) -> None:
    """finish_reason='length' emits exact truncation notice before done."""
    ensure_home(aldwin_home)
    scripted = ScriptedModel(
        [
            [
                Delta(content="Partial answer"),
                Delta(
                    finish_reason="length",
                    usage=Usage(input_tokens=10, output_tokens=5),
                ),
            ]
        ]
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=scripted),
        initial_model="anthropic/claude-3-5-sonnet",
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "write long text")
        notices = [ev.message for ev in events if isinstance(ev, NoticeEvent)]
        assert notices == [
            "The answer was cut off because it reached the model's length limit."
        ]
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].reason == "answered"
    finally:
        await client.close()
        await host.stop_host()


async def test_host_mid_stream_drop_no_retry(aldwin_home: Path) -> None:
    """Server error after text chunk yielded does not retry from scratch."""
    ensure_home(aldwin_home)

    class _MidStreamDropModel(FakeModel):
        async def stream(self, messages: Any, tools: Any = None) -> Any:
            self.calls.append(list(messages))
            yield Delta(content="First chunk")
            raise ModelServerError("Connection reset by peer", retryable=True)

    model = _MidStreamDropModel()
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(
            model=model,
            retry=RetryPolicy(max_retries=3, base_delay=0.01, jitter=0.0),
        ),
        initial_model="openai/gpt-4o",
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect_turn(client, "stream test")
        texts = [ev.delta for ev in events if isinstance(ev, TextEvent)]
        assert "".join(texts) == "First chunk"
        assert isinstance(events[-1], ErrorEvent)
        assert events[-1].code == "rate_limited"
        assert len(model.calls) == 1
    finally:
        await client.close()
        await host.stop_host()


def test_architecture_import_guard_doors_never_import_models() -> None:
    """CLI, Client, and Protocol never import classify_model_error or models."""
    root = Path(__file__).resolve().parent.parent / "src" / "aldwin"
    doors_and_protocol = [root / "cli", root / "client", root / "protocol"]

    for directory in doors_and_protocol:
        for py_path in directory.rglob("*.py"):
            text = py_path.read_text(encoding="utf-8")
            assert "classify_model_error" not in text, (
                f"{py_path} imports classify_model_error"
            )
            assert "aldwin.core.models" not in text, (
                f"{py_path} imports aldwin.core.models"
            )
