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

# dev 启动器创建的文件标志。venv launcher 派生子进程时可能丢失环境变量，
# 文件标志不受进程派生影响，作为 XIADIE_DEV_MODE 的可靠后备。
_DEV_MODE_FLAG = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".dev_mode")


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _dev_mode() -> bool:
    """dev 模式判断：环境变量或文件标志，任一为真即可。"""
    return _enabled("XIADIE_DEV_MODE") or os.path.exists(_DEV_MODE_FLAG)


def _authorized(request: Request) -> bool:
    expected = os.environ.get("XIADIE_API_TOKEN", "")
    provided = request.headers.get(TOKEN_HEADER, "")
    if expected and provided and secrets.compare_digest(provided, expected):
        return True

    # Browser-only development fallback. It is opt-in and limited to the exact
    # local Vite origins; packaged/file renderers must always use the token.
    return _dev_mode() and request.headers.get("origin") in DEV_ORIGINS


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
