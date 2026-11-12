"""The page the browser shows after sign-in, in Aldwin's brand.

Self-contained (no fonts, scripts or images from elsewhere): the address it is
served on carries the sign-in code, so the page must not make other requests.
"""

from __future__ import annotations

from html import escape
from typing import Final, Literal
from urllib.parse import quote

PageKind = Literal["ok", "declined", "stale"]

# Response headers: nothing loads from elsewhere, and the address never leaks.
PAGE_HEADERS: Final[tuple[tuple[str, str], ...]] = (
    ("Content-Type", "text/html; charset=utf-8"),
    (
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
        "base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'",
    ),
    ("Referrer-Policy", "no-referrer"),
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
)

_SILHOUETTE = (
    "M128,36 C206,34 230,58 231,130 C232,204 210,226 128,226 "
    "C46,226 24,206 25,132 C26,60 50,38 128,36 Z"
)
_STROKE = (
    'fill="none" stroke="#000" stroke-width="14" '
    'stroke-linecap="square" stroke-linejoin="miter"'
)
# The eyes of each expression, cut out of the mark (from the brand kit).
_EYES: Final[dict[PageKind, str]] = {
    "ok": (
        f'<polyline points="80,124 96,104 112,124" {_STROKE}/>'
        f'<polyline points="144,124 160,104 176,124" {_STROKE}/>'
    ),
    "declined": (
        f'<polyline points="84,96 106,112 84,128" {_STROKE}/>'
        f'<polyline points="172,96 150,112 172,128" {_STROKE}/>'
    ),
    "stale": (
        '<rect x="84" y="88" width="24" height="48" fill="#000"/>'
        '<rect x="148" y="88" width="24" height="48" fill="#000"/>'
    ),
}

# The web app's tokens (web/src/styles/tokens.css): neutral paper and ink on one
# hue. This page is served on its own port, so it cannot read the theme the
# user picked in Aldwin; it follows the system's light or dark setting.
_STYLE = """
:root {
  color-scheme: light dark;
  --tint: 106;
  --background: oklch(0.992 0.002 var(--tint));
  --foreground: oklch(0.145 0.006 var(--tint));
  --muted: oklch(0.97 0.004 var(--tint));
  --muted-foreground: oklch(0.556 0.007 var(--tint));
  --border: oklch(0.922 0.005 var(--tint));
}
@media (prefers-color-scheme: dark) {
  :root {
    --background: oklch(0.17 0.003 var(--tint));
    --foreground: oklch(0.985 0.002 var(--tint));
    --muted: oklch(0.275 0.004 var(--tint));
    --muted-foreground: oklch(0.708 0.004 var(--tint));
    --border: oklch(0.985 0.002 var(--tint) / 10%);
  }
}
* { box-sizing: border-box; margin: 0; }
html, body { height: 100%; }
body {
  display: grid; place-items: center; padding: 24px;
  background: var(--background); color: var(--foreground);
  font: 16px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  -webkit-font-smoothing: antialiased; text-align: center;
}
main {
  width: 100%; max-width: 26rem; padding: 40px 28px;
  border: 1px solid var(--border); border-radius: 16px;
  animation: arrive 320ms cubic-bezier(.2,.8,.2,1) both;
}
.mark { width: 64px; height: 64px; margin: 0 auto 24px; display: block; }
.mark.ok { color: var(--foreground); }
.mark.declined, .mark.stale { color: var(--muted-foreground); }
h1 {
  font-size: 1.375rem; line-height: 1.3; font-weight: 600;
  letter-spacing: -0.01em;
}
p { margin-top: 8px; color: var(--muted-foreground); text-wrap: balance; }
code {
  font: 0.9em ui-monospace, "SF Mono", Menlo, monospace;
  background: var(--muted); padding: 2px 6px; border-radius: 6px;
  color: var(--foreground);
}
footer {
  margin-top: 28px; font-size: 13px; color: var(--muted-foreground);
}
@keyframes arrive { from { opacity: 0; transform: translateY(6px); } }
@media (prefers-reduced-motion: reduce) { main { animation: none; } }
"""


def _mark(kind: PageKind, *, fill: str = "currentColor", css: bool = True) -> str:
    attrs = (
        f'class="mark {kind}" aria-hidden="true"'
        if css
        else ('xmlns="http://www.w3.org/2000/svg"')
    )
    return (
        f'<svg {attrs} viewBox="0 0 256 256">'
        f'<defs><mask id="m" maskUnits="userSpaceOnUse" x="0" y="0" width="256" '
        f'height="256"><rect width="256" height="256" fill="#000"/>'
        f'<path d="{_SILHOUETTE}" fill="#fff"/>{_EYES[kind]}</mask></defs>'
        f'<rect width="256" height="256" fill="{fill}" mask="url(#m)"/></svg>'
    )


def _copy(kind: PageKind, title: str | None) -> tuple[str, str]:
    """The heading and the line under it; both already HTML-safe."""
    name = escape(title) if title else None
    retry = (
        "use Authenticate in Aldwin's Settings, under Tools, "
        "or run <code>/connectors</code> in the terminal"
    )
    if kind == "ok":
        heading = f"{name} is connected" if name else "You're signed in"
        return heading, "Go back to Aldwin. You can close this tab."
    if kind == "declined":
        return (
            "Sign-in was declined",
            f"Nothing was connected. To try again, {retry}.",
        )
    return (
        "This sign-in link has expired",
        f"To start again, {retry}.",
    )


def callback_page(kind: PageKind, title: str | None = None) -> str:
    """The whole page for one outcome, with the connector's title when known."""
    heading, line = _copy(kind, title)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>Aldwin · {heading}</title>"
        f'<link rel="icon" href="data:image/svg+xml,{_favicon()}">'
        f"<style>{_STYLE}</style></head>"
        f"<body><main>{_mark(kind)}<h1>{heading}</h1><p>{line}</p>"
        "<footer>Aldwin</footer></main></body></html>"
    )


def _favicon() -> str:
    return quote(_mark("stale", fill="#1f1f1d", css=False), safe="")
