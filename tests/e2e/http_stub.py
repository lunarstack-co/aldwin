"""Local HTTP stub serving a small PDF for demo 4 (SSRF-safe public URL)."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class _Handler(BaseHTTPRequestHandler):
    pdf_bytes: bytes = b""

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/report.pdf", "/"):
            body = self.pdf_bytes
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/redirect-private":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1/secret")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


def start_pdf_stub(
    pdf_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
) -> tuple[HTTPServer, str]:
    """Serve `pdf_path` and return `(server, base_url)`."""
    data = pdf_path.read_bytes()

    class Handler(_Handler):
        pdf_bytes = data

    server = HTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bound = server.server_address[1]
    return server, f"http://{host}:{bound}"
