import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from redis.asyncio.client import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.response import ErrorResponse, ResponseSchema, SuccessResponse
from app.config.setting import settings
from app.core.base_schema import (
    AuthSchema,
    JWTOutSchema,
    RefreshTokenPayloadSchema,
)
from app.core.dependencies import db_getter, get_current_user, redis_getter
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.redis_crud import RedisCURD
from app.core.router_class import OperationLogRoute
from app.core.security import CustomOAuth2PasswordRequestForm

from .oauth_service import (
    STATE_PREFIX,
    _callback_url,
    build_authorize_url,
    complete_oauth_login,
    exchange_oauth_ticket,
    oauth_service_error_redirect,
    oauth_service_frontend_redirect_from_ticket,
    safe_frontend_redirect,
    save_oauth_state,
    save_oauth_ticket,
)
from .schema import (
    AutoLoginTokenSchema,
    AutoLoginUserSchema,
    CaptchaOutSchema,
    LoginSchema,
    OAuthTicketSchema,
)
from .service import (
    AutoLoginService,
    CaptchaService,
    LoginService,
)

AuthRouter = APIRouter(route_class=OperationLogRoute, prefix="/auth", tags=["系统管理", "认证授权"])


@AuthRouter.post(
    "/login",
    summary="登录",
    response_model=LoginSchema,
)
async def login_for_access_token_controller(
    request: Request,
    redis: Annotated[Redis, Depends(redis_getter)],
    login_form: Annotated[CustomOAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(db_getter, scope="function")],
) -> JSONResponse | dict:
    login_result = await LoginService.authenticate_user(request=request, redis=redis, login_form=login_form, db=db)

    logger.info(f"用户{login_form.username}登录成功")

    if settings.DOCS_URL in request.headers.get("referer", ""):
        return login_result
    return SuccessResponse(data=login_result, msg="登录成功")


@AuthRouter.post(
    "/token/refresh",
    summary="刷新token",
    response_model=ResponseSchema[JWTOutSchema],
)
async def get_new_token_controller(
    payload: RefreshTokenPayloadSchema,
    db: Annotated[AsyncSession, Depends(db_getter, scope="function")],
    redis: Annotated[Redis, Depends(redis_getter)],
) -> JSONResponse:
    new_token = await LoginService.refresh_token(db=db, redis=redis, refresh_token=payload)
    return SuccessResponse(data=new_token, msg="刷新成功")


@AuthRouter.get(
    "/captcha/get",
    summary="获取验证码",
    response_model=ResponseSchema[CaptchaOutSchema],
)
async def get_captcha_for_login_controller(
    redis: Annotated[Redis, Depends(redis_getter)],
) -> JSONResponse:
    captcha = await CaptchaService.get_captcha(redis=redis)
    return SuccessResponse(data=captcha, msg="获取验证码成功")


@AuthRouter.post(
    "/logout",
    summary="退出登录",
    dependencies=[Depends(get_current_user)],
    response_model=ResponseSchema[None],
)
async def logout_controller(
    request: Request,
    auth: Annotated[AuthSchema, Depends(get_current_user)],
    redis: Annotated[Redis, Depends(redis_getter)],
) -> JSONResponse:
    session_id = getattr(getattr(request.state, "ctx", None), "session_id", None)
    if await LoginService.logout(redis=redis, session_id=session_id or ""):
        logger.info("退出成功")
        return SuccessResponse(msg="退出成功")
    return ErrorResponse(msg="退出失败")


@AuthRouter.get(
    "/auto-login/users",
    summary="获取免登录用户列表",
    response_model=ResponseSchema[list[AutoLoginUserSchema]],
)
async def get_auto_login_users_controller(
    auth: Annotated[AuthSchema, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(db_getter, scope="function")],
) -> JSONResponse:
    if not auth.user.is_superuser:
        raise CustomException(msg="仅超级管理员可查看免登录用户")
    users = await AutoLoginService.get_auto_login_users(db=db)
    return SuccessResponse(data=users, msg="获取成功")


@AuthRouter.post(
    "/auto-login/token",
    summary="获取免登录Token",
    response_model=ResponseSchema[AutoLoginTokenSchema],
)
async def get_auto_login_token_controller(
    auth: Annotated[AuthSchema, Depends(get_current_user)],
    redis: Annotated[Redis, Depends(redis_getter)],
    db: Annotated[AsyncSession, Depends(db_getter, scope="function")],
    user_id: int,
) -> JSONResponse:
    if not auth.user.is_superuser:
        raise CustomException(msg="仅超级管理员可生成免登录Token")
    result = await AutoLoginService.create_auto_login_token(redis=redis, db=db, user_id=user_id)
    return SuccessResponse(data=result, msg="获取成功")


@AuthRouter.post(
    "/auto-login",
    summary="免登录",
    response_model=ResponseSchema[JWTOutSchema],
)
async def auto_login_controller(
    request: Request,
    redis: Annotated[Redis, Depends(redis_getter)],
    db: Annotated[AsyncSession, Depends(db_getter, scope="function")],
    token: str,
) -> JSONResponse:
    login_token = await AutoLoginService.auto_login(request=request, redis=redis, db=db, token=token)
    logger.info("用户免登录成功")
    return SuccessResponse(data=login_token, msg="登录成功")




@AuthRouter.get(
    "/oauth/{provider}/login",
    summary="第三方OAuth跳转",
)
async def oauth_login_redirect_controller(
    request: Request,
    redis: Annotated[Redis, Depends(redis_getter)],
    provider: Annotated[str, Path(description="wechat | qq | github | gitee")],
    redirect_uri: Annotated[
        str | None,
        Query(description="OAuth 完成后浏览器回到的前端登录页完整 URL"),
    ] = None,
) -> RedirectResponse:
    allowed = {"wechat", "qq", "github", "gitee"}
    fe = safe_frontend_redirect(redirect_uri)
    if redirect_uri and fe == settings.OAUTH_FRONTEND_FALLBACK and redirect_uri.strip() != fe:
        return RedirectResponse(
            url=oauth_service_error_redirect(settings.OAUTH_FRONTEND_FALLBACK, "非法的回调地址"),
            status_code=302,
        )
    if provider not in allowed:
        return RedirectResponse(
            url=oauth_service_error_redirect(fe, "不支持的 OAuth 渠道"),
            status_code=302,
        )
    if not redirect_uri:
        return RedirectResponse(
            url=oauth_service_error_redirect(settings.OAUTH_FRONTEND_FALLBACK, "缺少 redirect_uri 参数"),
            status_code=302,
        )
    try:
        state = secrets.token_urlsafe(32)
        await save_oauth_state(
            redis=redis,
            state=state,
            provider=provider,
            frontend_redirect=fe,
        )
        cb = _callback_url(request, provider)
        url = build_authorize_url(provider=provider, callback_url=cb, state=state)
        return RedirectResponse(url=url, status_code=302)
    except CustomException as e:
        return RedirectResponse(
            url=oauth_service_error_redirect(fe, e.msg),
            status_code=302,
        )


@AuthRouter.get(
    "/oauth/{provider}/callback",
    summary="第三方OAuth回调",
    include_in_schema=False,
)
async def oauth_callback_controller(
    request: Request,
    redis: Annotated[Redis, Depends(redis_getter)],
    db: Annotated[AsyncSession, Depends(db_getter, scope="function")],
    provider: Annotated[str, Path()],
    code: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
) -> RedirectResponse:
    fe_fallback = settings.OAUTH_FRONTEND_FALLBACK

    async def resolve_frontend() -> str:
        if not state:
            return fe_fallback
        raw = await RedisCURD(redis).get(f"{STATE_PREFIX}{state}")
        if not raw:
            return fe_fallback
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            payload = json.loads(raw)
            return str(payload.get("frontend_redirect") or fe_fallback).strip() or fe_fallback
        except json.JSONDecodeError:
            return fe_fallback

    if provider not in {"wechat", "qq", "github", "gitee"}:
        url = oauth_service_error_redirect(await resolve_frontend(), "不支持的 OAuth 渠道")
        return RedirectResponse(url=url, status_code=302)
    if not code or not state:
        url = oauth_service_error_redirect(await resolve_frontend(), "授权被取消或参数不完整")
        return RedirectResponse(url=url, status_code=302)
    try:
        token, fe = await complete_oauth_login(
            request=request,
            redis=redis,
            db=db,
            provider=provider,
            code=code,
            state=state,
        )
        ticket = await save_oauth_ticket(redis, token)
        success_url = oauth_service_frontend_redirect_from_ticket(fe, ticket)
        return RedirectResponse(url=success_url, status_code=302)
    except CustomException as e:
        fe = await resolve_frontend()
        return RedirectResponse(url=oauth_service_error_redirect(fe, e.msg), status_code=302)


@AuthRouter.post(
    "/oauth/exchange",
    summary="兑换 OAuth 一次性登录凭证",
    response_model=ResponseSchema[JWTOutSchema],
)
async def oauth_ticket_exchange_controller(
    payload: OAuthTicketSchema,
    redis: Annotated[Redis, Depends(redis_getter)],
) -> JSONResponse:
    """将浏览器安全的 OAuth 票据一次性兑换为 JWT。"""
    token = await exchange_oauth_ticket(redis=redis, ticket=payload.ticket)
    return SuccessResponse(data=token, msg="OAuth 登录成功")
