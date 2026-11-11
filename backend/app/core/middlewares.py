import time
import uuid
from dataclasses import replace

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.common.response import ErrorResponse
from app.config.setting import settings
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.request_context import RequestContext, reset_correlation_id, set_correlation_id
from app.core.security import decode_access_token


def _strip_bearer(authorization: str) -> str | None:
    """从 Authorization header 提取 token，非 Bearer 返回 None。"""
    v = authorization.strip()
    if v.lower().startswith("bearer "):
        v = v[7:].strip()
    elif v.lower().startswith("bearer"):
        v = v[6:].strip()
    else:
        return None
    return v or None


class CustomCORSMiddleware(CORSMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(
            app,
            allow_origins=settings.ALLOW_ORIGINS,
            allow_methods=settings.ALLOW_METHODS,
            allow_headers=settings.ALLOW_HEADERS,
            allow_credentials=settings.ALLOW_CREDENTIALS,
            expose_headers=settings.CORS_EXPOSE_HEADERS,
        )


class RequestLogMiddleware(BaseHTTPMiddleware):
    """请求日志与 IP 黑名单拦截。"""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    @staticmethod
    def _hydrate_session_id(request: Request) -> None:
        """从 request.state.ctx 或 JWT 中提取 session_id 并写入 ctx（纯 side-effect）。

        JWT sub 现为纯 session_id 字符串，无需 JSON 解析。
        """
        ctx = getattr(request.state, "ctx", None)
        if ctx:
            if ctx.session_id:
                return
            if ctx.jwt_user_info:
                sid = ctx.jwt_user_info.get("session_id")
                if sid:
                    request.state.ctx = replace(ctx, session_id=sid)
                    return

        token = _strip_bearer(request.headers.get("Authorization", ""))
        if not token:
            return
        try:
            payload = decode_access_token(token)
            if not payload or not hasattr(payload, "sub"):
                return
            sid = payload.sub
            if sid:
                base = ctx or RequestContext()
                request.state.ctx = replace(base, session_id=sid)
        except Exception as exc:
            # 未登录或 token 无效时不影响请求主流程，但保留排查线索
            logger.debug(f"解析请求上下文 session_id 失败: {exc}")

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start_time = time.time()
        self._hydrate_session_id(request)

        logger.info("请求: {} {} | client={}", request.method, request.url.path,
                     request.client.host if request.client else "unknown")

        try:
            response = await call_next(request)
            process_time = round(time.time() - start_time, 5)
            response.headers["X-Process-Time"] = str(process_time)
            logger.info("响应: {} | {:.1f}ms", response.status_code, process_time * 1000)
            return response
        except CustomException as e:
            logger.exception(f"中间件异常: {e!s}")
            return ErrorResponse(msg=e.msg, code=e.code, status_code=e.status_code)


class CustomGZipMiddleware(GZipMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app, minimum_size=settings.GZIP_MIN_SIZE, compresslevel=settings.GZIP_COMPRESS_LEVEL)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._hsts = settings.HSTS_ENABLE

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        if self._hsts:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), magnetometer=(), gyroscope=()"
        return response


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        self._header = "X-Correlation-ID"
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        cid = request.headers.get(self._header) or str(uuid.uuid4())
        token = set_correlation_id(cid)
        try:
            response = await call_next(request)
            response.headers[self._header] = cid
            return response
        finally:
            reset_correlation_id(token)
