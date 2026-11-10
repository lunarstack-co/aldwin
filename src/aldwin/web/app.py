"""The FastAPI app behind ``aldwin web``: the packaged UI plus the ``/v1`` API."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from aldwin.web.auth import COOKIE_NAME, TOKEN_QUERY, Authenticator

STATIC_DIR = Path(__file__).with_name("static")
API_PREFIX = "/v1"

SIGN_IN_MESSAGE = "Open Aldwin from the link that `aldwin web` printed."
FOREIGN_ORIGIN_MESSAGE = "This request did not come from the Aldwin page."


class StaticFilesMissingError(RuntimeError):
    """The packaged web UI is not on disk."""


class SessionGuard:
    """Rejects ``/v1`` requests from another site or port, or without the cookie."""

    def __init__(self, app: ASGIApp, authenticator: Authenticator) -> None:
        self._app = app
        self._auth = authenticator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _is_api_path(scope.get("path", "")):
            await self._app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # No WebSocket route has a check yet, so every upgrade is refused.
            await WebSocketClose(code=1008)(scope, receive, send)
            return
        request = Request(scope)
        headers = request.headers
        if not (
            self._auth.host_ok(headers.get("host"))
            and self._auth.origin_ok(headers.get("origin"))
            and self._auth.fetch_site_ok(headers.get("sec-fetch-site"))
        ):
            response: Response = _error(403, FOREIGN_ORIGIN_MESSAGE)
        elif not self._auth.token_ok(request.cookies.get(COOKIE_NAME)):
            response = _error(401, SIGN_IN_MESSAGE)
        else:
            await self._app(scope, receive, send)
            return
        await response(scope, receive, send)


def create_app(
    *,
    authenticator: Authenticator,
    home_display: str,
    version: str,
    static_dir: Path = STATIC_DIR,
) -> FastAPI:
    """Build the app that serves the web UI, the session bootstrap and ``/v1``."""
    index = static_dir / "index.html"
    if not index.is_file():
        raise StaticFilesMissingError(
            f"The web UI is missing from {static_dir}. "
            "Run `npm run build` in web/ to build it."
        )

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionGuard, authenticator=authenticator)

    api = APIRouter(prefix=API_PREFIX)

    @api.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True, "version": version}

    @api.get("/home")
    def home() -> dict[str, str]:
        return {"home": home_display, "version": version}

    app.include_router(api)

    @app.get("/", include_in_schema=False)
    def root(request: Request) -> Response:
        token = request.query_params.get(TOKEN_QUERY)
        if token is None:
            return FileResponse(index)
        if not authenticator.host_ok(request.headers.get("host")):
            return _error(403, FOREIGN_ORIGIN_MESSAGE)
        if not authenticator.token_ok(token):
            return _error(401, SIGN_IN_MESSAGE)
        # Redirecting drops the token from the address bar and browser history.
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(**authenticator.cookie_kwargs())
        response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/", StaticFiles(directory=static_dir, html=True), name="ui")
    return app


def _is_api_path(path: str) -> bool:
    return path == API_PREFIX or path.startswith(f"{API_PREFIX}/")


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"detail": message}, status_code=status)
