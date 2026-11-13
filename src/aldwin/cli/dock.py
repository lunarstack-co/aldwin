"""Docked prompt session: pinned input band, footer states, newline / Ctrl+C keys."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Final, Literal

from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import AnyFormattedText, FormattedText
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.shortcuts import PromptSession

FooterKind = Literal["idle", "interrupt", "ctrl_c", "paste", "shortcuts"]

CTRL_C_QUIT_WINDOW_S: Final[float] = 2.0
PLACEHOLDER: Final[str] = "Ask anything · / for commands · ? for shortcuts"

SHORTCUTS_PANEL: Final[tuple[tuple[str, str], ...]] = (
    ("↵", "send"),
    ("⌥↵ / ⇧↵", "newline"),
    ("↑↓", "history"),
    ("⌃R", "search history"),
    ("⌃G", "open in editor"),
    ("⌃O", "expand diff"),
    ("Esc", "stop / close"),
    ("⌃C", "clear / quit"),
    ("/", "commands"),
    ("@", "skills"),
)

_CSI_U_SHIFT_ENTER = "\x1b[13;2u"
_MODIFY_OTHER_KEYS_SHIFT_ENTER = "\x1b[27;2;13~"
_shift_enter_registered = False


def register_shift_enter_sequences() -> None:
    """Map Shift+Enter CSI-u / modifyOtherKeys sequences to newline (ControlJ).

    prompt_toolkit maps modifyOtherKeys Shift+Enter to ControlM (submit); override
    so Shift+Enter inserts a newline. Idempotent; call from session setup.
    """
    global _shift_enter_registered
    if _shift_enter_registered:
        return
    ANSI_SEQUENCES[_CSI_U_SHIFT_ENTER] = Keys.ControlJ
    ANSI_SEQUENCES[_MODIFY_OTHER_KEYS_SHIFT_ENTER] = Keys.ControlJ
    _shift_enter_registered = True


class AldwinPromptSession(PromptSession[str]):
    """PromptSession that does not stretch empty rows between input and footer."""

    def __init__(
        self,
        *args: object,
        on_quit: Callable[[], None] | None = None,
        footer_right: Callable[[], str | FormattedText] | None = None,
        paste_store: object | None = None,
        **kwargs: object,
    ) -> None:
        register_shift_enter_sequences()
        self._on_quit = on_quit
        self._footer_right = footer_right or (lambda: "")
        self._paste_store = paste_store
        self._footer_kind: FooterKind = "idle"
        self._footer_left_override: str | None = None
        self._ctrl_c_at: float = 0.0
        self._shortcuts_open: bool = False
        kwargs.setdefault("reserve_space_for_menu", 0)
        # Bottom toolbar refreshes from our contextual footer.
        kwargs["bottom_toolbar"] = self._toolbar_text
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def _get_default_buffer_control_height(self) -> Dimension:
        """Do not reserve blank rows for a completion menu above the dock."""
        return Dimension()

    def set_footer(
        self,
        kind: FooterKind,
        *,
        left: str | None = None,
    ) -> None:
        """Set contextual left-side footer state."""
        self._footer_kind = kind
        self._footer_left_override = left
        if kind != "shortcuts":
            self._shortcuts_open = False

    def set_busy(self, busy: bool) -> None:
        """While a turn runs, show interrupt hint (type-ahead still queues)."""
        if busy:
            self.set_footer("interrupt", left="esc to interrupt")
        else:
            self.set_footer("idle")

    def _left_hint(self) -> str:
        if self._footer_left_override:
            return self._footer_left_override
        if self._footer_kind == "interrupt":
            return "esc to interrupt"
        if self._footer_kind == "ctrl_c":
            return "Press Ctrl-C again to exit"
        if self._footer_kind == "paste":
            return "paste again to expand"
        if self._footer_kind == "shortcuts":
            return " · ".join(f"{k} {v}" for k, v in SHORTCUTS_PANEL[:5])
        return "? for shortcuts"

    def _toolbar_text(self) -> AnyFormattedText:
        left = f" {self._left_hint()} "
        right = self._footer_right()
        if isinstance(right, str):
            right_plain = right.rstrip()
            if not right_plain:
                return left
            # prompt_toolkit bottom_toolbar is one line; right side appended with gap.
            return f"{left}  {right_plain}"
        fragments = list(right)
        if not fragments:
            return left
        return FormattedText([("", f"{left}  "), *fragments])

    def attach_key_bindings(self, extra: KeyBindings | None = None) -> KeyBindings:
        """Return key bindings including Ctrl+C clear/quit and newline helpers."""
        kb = extra or KeyBindings()

        @kb.add("escape", "enter")
        def _alt_enter(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text("\n")

        @kb.add("c-j")
        def _ctrl_j(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text("\n")

        @kb.add("c-g")
        def _ctrl_g(event: KeyPressEvent) -> None:
            buf = event.current_buffer

            if buf.complete_state is not None:
                return

            buf.open_in_editor()

        @kb.add("c-m")
        def _enter(event: KeyPressEvent) -> None:
            buf = event.current_buffer
            if buf.document.text_before_cursor.endswith("\\"):
                buf.delete_before_cursor(count=1)
                buf.insert_text("\n")
            else:
                buf.validate_and_handle()

        @kb.add("c-c")
        def _ctrl_c(event: KeyPressEvent) -> None:
            buf = event.current_buffer
            if buf.text:
                # Keep in history via prompt_toolkit history on validate; clear draft.
                if buf.text.strip():
                    try:
                        event.app.current_buffer.history.append_string(buf.text)
                    except (OSError, ValueError, TypeError):  # pragma: no cover
                        pass
                buf.reset()
                self._ctrl_c_at = 0.0
                self.set_footer("idle")
                return
            now = time.monotonic()
            if now - self._ctrl_c_at <= CTRL_C_QUIT_WINDOW_S:
                if self._on_quit is not None:
                    self._on_quit()
                event.app.exit(exception=EOFError())
                return
            self._ctrl_c_at = now
            self.set_footer("ctrl_c", left="Press Ctrl-C again to exit")

        @kb.add("escape")
        def _esc(event: KeyPressEvent) -> None:
            # Not eager: allow `escape`+`enter` (Alt+Enter) to insert a newline.
            if self._shortcuts_open:
                self._shortcuts_open = False
                self.set_footer("idle")
                return
            buf = event.current_buffer
            # Double-Esc on a non-empty idle prompt clears the draft.
            if buf.text:
                now = time.monotonic()
                last = getattr(self, "_esc_at", 0.0)
                if now - last <= CTRL_C_QUIT_WINDOW_S:
                    buf.reset()
                    self._esc_at = 0.0
                else:
                    self._esc_at = now
                return

        @kb.add("?", filter=Condition(lambda: not self.default_buffer.text))
        def _question(event: KeyPressEvent) -> None:
            # Replace footer with shortcuts; do not insert '?'.
            self._shortcuts_open = True
            self.set_footer("shortcuts")

        @kb.add("<any>", filter=Condition(lambda: self._shortcuts_open))
        def _close_shortcuts(event: KeyPressEvent) -> None:
            # Next key after `?` restores the footer and is not inserted.
            self._shortcuts_open = False
            self.set_footer("idle")
            key = event.key_sequence[-1].key if event.key_sequence else ""
            if key in ("escape", "c-c"):
                return

        @kb.add(Keys.BracketedPaste)
        def _bracketed_paste(event: KeyPressEvent) -> None:
            # Replace default paste handling (one insert only).
            raw = event.data or ""
            data = raw.replace("\r\n", "\n").replace("\r", "\n")
            store = self._paste_store
            should_collapse = getattr(store, "should_collapse", None)
            collapse = getattr(store, "collapse", None)
            if (
                store is not None
                and callable(should_collapse)
                and should_collapse(data)
                and callable(collapse)
            ):
                token = collapse(data)
                event.current_buffer.insert_text(str(token))
                self.set_footer("paste", left="paste again to expand")
                return
            # Second paste while footer is paste → expand last token in buffer.
            expand_last = getattr(store, "expand_last", None)
            if (
                store is not None
                and self._footer_kind == "paste"
                and callable(expand_last)
            ):
                expanded = expand_last()
                if expanded is not None:
                    from aldwin.cli.paste import TOKEN_RE

                    buf = event.current_buffer
                    text = buf.text
                    match = None
                    for match in TOKEN_RE.finditer(text):
                        pass
                    if match is not None:
                        buf.text = (
                            text[: match.start()] + expanded + text[match.end() :]
                        )
                        buf.cursor_position = match.start() + len(expanded)
                        self.set_footer("idle")
                        return
            event.current_buffer.insert_text(data)

        return kb
