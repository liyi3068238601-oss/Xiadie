"""Local API access boundary shared by every FastAPI route."""

import os
import secrets

from fastapi import Request
from starlette.responses import JSONResponse

TOKEN_HEADER = "X-Xiadie-Token"
PUBLIC_PATHS = frozenset({"/api/health"})
ALLOWED_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "null",  # Electron production renderer loaded from file://
)
DEV_ORIGINS = frozenset(ALLOWED_ORIGINS[:2])


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _authorized(request: Request) -> bool:
    expected = os.environ.get("XIADIE_API_TOKEN", "")
    provided = request.headers.get(TOKEN_HEADER, "")
    if expected and provided and secrets.compare_digest(provided, expected):
        return True

    # Browser-only development fallback. It is opt-in and limited to the exact
    # local Vite origins; packaged/file renderers must always use the token.
    return _enabled("XIADIE_DEV_MODE") and request.headers.get("origin") in DEV_ORIGINS


async def local_api_guard(request: Request, call_next):
    if (
        request.method == "OPTIONS"
        or request.url.path in PUBLIC_PATHS
        or not request.url.path.startswith("/api/")
        or _authorized(request)
    ):
        return await call_next(request)

    return JSONResponse(
        status_code=401,
        content={"detail": "未授权的本地 API 请求"},
        headers={"Cache-Control": "no-store"},
    )
