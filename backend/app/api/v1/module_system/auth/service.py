
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta
from typing import NewType

import ua_parser
from fastapi import Request
from redis.asyncio.client import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.module_system.user.crud import UserCRUD
from app.api.v1.module_system.user.model import UserModel
from app.common.enums import RedisInitKeyConfig
from app.config.setting import settings
from app.core.base_schema import (
    AuthSchema,
    JWTOutSchema,
    JWTPayloadSchema,
    RefreshTokenPayloadSchema,
)
from app.core.client_ip import get_client_ip
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.redis_crud import RedisCURD
from app.core.security import (
    CustomOAuth2PasswordRequestForm,
    create_access_token,
    credential_fingerprint,
    decode_access_token,
    verify_session_credentials,
)
from app.utils.captcha_util import CaptchaUtil
from app.utils.common_util import get_random_character
from app.utils.hash_bcrpy_util import PwdUtil
from app.utils.ip_local_util import IpLocalUtil

from .schema import (
    AutoLoginTokenSchema,
    AutoLoginUserSchema,
    CaptchaOutSchema,
    LoginSchema,
)

CaptchaKey = NewType("CaptchaKey", str)
CaptchaBase64 = NewType("CaptchaBase64", str)


async def _write_login_log(
    db: AsyncSession,
    *,
    username: str,
    status: int,
    login_ip: str | None = None,
    login_location: str | None = None,
    request_os: str | None = None,
    request_browser: str | None = None,
    msg: str | None = None,
) -> None:
    """写入登录日志并立即提交。

    日志与登录请求共用会话：另开连接时，本请求已持有的事务会阻塞这次写入
    （SQLite 只能有一个写者，实测等 5s 后抛 `database is locked`），日志会被丢弃。
    写入后立即提交，失败登录的日志在后续回滚时也不会丢失。
    """
    from app.api.v1.module_system.log.crud import LoginLogCRUD
    from app.api.v1.module_system.log.schema import LoginLogCreateSchema

    try:
        await LoginLogCRUD(AuthSchema(db=db, check_data_scope=False)).create(
            data=LoginLogCreateSchema(
                username=username,
                status=status,
                login_ip=login_ip,
                login_location=login_location,
                request_os=request_os,
                request_browser=request_browser,
                msg=msg,
            )
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.warning(f"登录日志写入失败: {exc}")


def _resolve_request_ip(request: Request) -> str:
    """从请求中解析客户端真实 IP。"""
    return get_client_ip(request)


def _login_subject_digest(username: str, request_ip: str) -> str:
    """对登录主体和可信客户端地址取哈希，供 Redis 键名使用。"""
    subject = f"{username.strip().lower()}\x00{request_ip.strip()}"
    return hashlib.sha256(subject.encode("utf-8")).hexdigest()


def _login_failure_key(username: str, request_ip: str) -> str:
    """生成不暴露原始登录输入的 Redis 键名。"""
    digest = _login_subject_digest(username, request_ip)
    return f"auth:login:failure:{digest}"


def _login_lock_key(username: str, request_ip: str) -> str:
    """返回 Redis 短期账号锁的键名。"""
    digest = _login_subject_digest(username, request_ip)
    return f"auth:login:lock:{digest}"


async def _get_login_failure_count(redis: Redis, username: str, request_ip: str) -> int:
    """读取指定账号与客户端组合的当前登录失败次数。"""
    if await RedisCURD(redis).exists(_login_lock_key(username, request_ip)):
        return settings.LOGIN_MAX_FAILURES
    value = await RedisCURD(redis).get(_login_failure_key(username, request_ip))
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


async def _record_login_failure(redis: Redis, username: str, request_ip: str) -> int:
    """递增账号登录失败计数并设置过期时间。

    优先使用 Redis 原子操作 ``INCR``；兼容路径用于轻量测试替身及非标准 Redis 客户端。
    """
    key = _login_failure_key(username, request_ip)
    try:
        count = int(await redis.incr(key))
        await redis.expire(key, settings.LOGIN_FAILURE_WINDOW_SECONDS)
        if count >= settings.LOGIN_MAX_FAILURES:
            await RedisCURD(redis).set(
                _login_lock_key(username, request_ip),
                "1",
                expire=settings.LOGIN_LOCK_SECONDS,
            )
        return count
    except (AttributeError, TypeError, ValueError):
        count = await _get_login_failure_count(redis, username, request_ip) + 1
        await RedisCURD(redis).set(key, str(count), expire=settings.LOGIN_FAILURE_WINDOW_SECONDS)
        if count >= settings.LOGIN_MAX_FAILURES:
            await RedisCURD(redis).set(
                _login_lock_key(username, request_ip),
                "1",
                expire=settings.LOGIN_LOCK_SECONDS,
            )
        return count


async def _clear_login_failures(redis: Redis, username: str, request_ip: str) -> None:
    """在登录成功后清除对应账号与客户端的失败计数。"""
    await RedisCURD(redis).delete(
        _login_failure_key(username, request_ip),
        _login_lock_key(username, request_ip),
    )


def _locked_message() -> str:
    """返回对客户端展示的固定账号锁定提示。"""
    return "登录失败次数过多，请稍后再试"


class LoginService:
    """登录认证服务。"""

    def __init__(self, auth: AuthSchema | None = None) -> None:
        self.auth = auth

    @classmethod
    async def authenticate_user(
        cls,
        request: Request,
        redis: Redis,
        login_form: CustomOAuth2PasswordRequestForm,
        db: AsyncSession,
    ) -> LoginSchema:
        """用户认证。"""
        ua_result = ua_parser.parse(request.headers.get("user-agent"))
        request_ip = _resolve_request_ip(request)
        login_location = await IpLocalUtil.resolve_location_for_log(request_ip)
        _login_os = ua_result.os.family if ua_result.os else "Unknown"
        _login_browser = ua_result.user_agent.family if ua_result.user_agent else "Unknown"
        _login_username = login_form.username

        failure_count = await _get_login_failure_count(redis, _login_username, request_ip)
        if failure_count >= settings.LOGIN_MAX_FAILURES:
            raise CustomException(msg=_locked_message(), status_code=429)

        captcha_required = settings.CAPTCHA_ENABLE or failure_count >= settings.LOGIN_CAPTCHA_AFTER_FAILURES
        if captcha_required:
            if not login_form.captcha_key or not login_form.captcha:
                await _record_login_failure(redis, _login_username, request_ip)
                raise CustomException(msg="验证码不能为空", status_code=429 if failure_count + 1 >= settings.LOGIN_MAX_FAILURES else 400)
            try:
                await CaptchaService.check_captcha(
                    redis=redis,
                    key=login_form.captcha_key,
                    captcha=login_form.captcha,
                )
            except CustomException:
                await _record_login_failure(redis, _login_username, request_ip)
                raise

        auth = AuthSchema(db=db, check_data_scope=False)
        user = await UserCRUD(auth).get(username=login_form.username)

        if not user:
            failure_count = await _record_login_failure(redis, _login_username, request_ip)
            await _write_login_log(
                db=db,
                username=_login_username,
                status=2,
            )
            raise CustomException(
                msg=_locked_message() if failure_count >= settings.LOGIN_MAX_FAILURES else "账号或密码错误",
                status_code=429 if failure_count >= settings.LOGIN_MAX_FAILURES else 401,
            )

        if not PwdUtil.verify_password(plain_password=login_form.password, password_hash=user.password):
            failure_count = await _record_login_failure(redis, _login_username, request_ip)
            await _write_login_log(
                db=db,
                username=_login_username,
                status=2,
                login_ip=request_ip,
                login_location=login_location,
                request_os=_login_os,
                request_browser=_login_browser,
                msg="账号或密码错误",
            )
            raise CustomException(
                msg=_locked_message() if failure_count >= settings.LOGIN_MAX_FAILURES else "账号或密码错误",
                status_code=429 if failure_count >= settings.LOGIN_MAX_FAILURES else 401,
            )
        if user.status == 1:
            await _write_login_log(
                db=db,
                username=_login_username,
                status=2,
                login_ip=request_ip,
                login_location=login_location,
                request_os=_login_os,
                request_browser=_login_browser,
                msg="用户已被停用",
            )
            raise CustomException(msg="用户已被停用")
        await _clear_login_failures(redis, _login_username, request_ip)
        await UserCRUD(auth).update_last_login(id=user.id)

        if not user:
            raise CustomException(msg="用户不存在")
        if not login_form.login_type:
            raise CustomException(msg="登录类型不能为空")

        token = await cls.create_token(
            request=request,
            redis=redis,
            user=user,
            login_type=login_form.login_type,
        )
        user_info = {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "avatar": user.avatar,
            "is_superuser": user.is_superuser,
        }

        await _write_login_log(
            db=db,
            username=user.username,
            status=1,
            login_ip=request_ip,
            login_location=login_location,
            request_os=_login_os,
            request_browser=_login_browser,
            msg="登录成功",
        )

        return LoginSchema(
            access_token=token.access_token,
            refresh_token=token.refresh_token,
            expires_in=token.expires_in,
            token_type=token.token_type,
            user_info=user_info,
        )

    @classmethod
    async def create_token(cls, request: Request, redis: Redis, user: UserModel, login_type: str) -> JWTOutSchema:
        """创建访问令牌和刷新令牌。"""
        session_id = str(uuid.uuid4())
        request_ip = _resolve_request_ip(request)

        login_location = await IpLocalUtil.resolve_location_for_log(request_ip)

        from dataclasses import replace

        from app.core.request_context import RequestContext

        base_ctx = getattr(request.state, "ctx", None) or RequestContext()
        request.state.ctx = replace(
            base_ctx,
            session_id=session_id,
            user_username=user.username,
            login_location=login_location,
        )

        access_expires = timedelta(seconds=settings.ACCESS_TOKEN_EXPIRE_SECONDS)
        refresh_expires = timedelta(seconds=settings.REFRESH_TOKEN_EXPIRE_SECONDS)

        now = datetime.now()
        session_info = {
            "session_id": session_id,
            "user_id": user.id,
            "username": user.username,
            "user_name": user.username,
            "login_type": login_type,
            "login_time": now.isoformat(),
            "credential_fingerprint": credential_fingerprint(user.id, user.password),
        }
        access_token = create_access_token(
            payload=JWTPayloadSchema(
                sub=session_id,
                is_refresh=False,
                exp=now + access_expires,
                jti=secrets.token_urlsafe(16),
            )
        )
        refresh_token = create_access_token(
            payload=JWTPayloadSchema(
                sub=session_id,
                is_refresh=True,
                exp=now + refresh_expires,
                jti=secrets.token_urlsafe(16),
            )
        )

        await RedisCURD(redis).set(
            key=f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}",
            value=access_token,
            expire=settings.ACCESS_TOKEN_EXPIRE_SECONDS,
        )

        await RedisCURD(redis).set(
            key=f"{RedisInitKeyConfig.REFRESH_TOKEN.key}:{session_id}",
            value=refresh_token,
            expire=settings.REFRESH_TOKEN_EXPIRE_SECONDS,
        )

        await RedisCURD(redis).set(
            key=f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}",
            value=session_info,
            expire=settings.REFRESH_TOKEN_EXPIRE_SECONDS,
        )

        return JWTOutSchema(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_SECONDS,
            token_type=settings.TOKEN_TYPE,
        )

    @classmethod
    async def refresh_token(
        cls,
        db: AsyncSession,
        redis: Redis,
        refresh_token: RefreshTokenPayloadSchema,
    ) -> JWTOutSchema:
        """刷新访问令牌。"""
        token_payload: JWTPayloadSchema = decode_access_token(token=refresh_token.refresh_token)
        if not token_payload.is_refresh:
            raise CustomException(msg="非法凭证，请传入刷新令牌")

        session_id = token_payload.sub
        session_info = await RedisCURD(redis).get(
            f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}"
        )
        if not session_info:
            raise CustomException(msg="会话已过期，请重新登录")

        try:
            session_data = json.loads(session_info)
        except (TypeError, json.JSONDecodeError):
            raise CustomException(msg="会话已失效，请重新登录")

        user_id = session_data.get("user_id")

        if not session_id or not user_id:
            raise CustomException(msg="非法凭证,无法获取会话编号或用户ID")

        auth = AuthSchema(db=db, check_data_scope=False)
        user = await UserCRUD(auth).get(id=user_id)
        if not user:
            raise CustomException(msg="刷新token失败，用户不存在")
        if user.status == 1:
            raise CustomException(msg="用户已被停用")

        verify_session_credentials(session_data, user.id, user.password)
        refresh_key = f"{RedisInitKeyConfig.REFRESH_TOKEN.key}:{session_id}"
        if not await RedisCURD(redis).compare_and_delete(refresh_key, refresh_token.refresh_token):
            raise CustomException(msg="刷新令牌已失效，请重新登录")

        access_expires = timedelta(seconds=settings.ACCESS_TOKEN_EXPIRE_SECONDS)
        refresh_expires = timedelta(seconds=settings.REFRESH_TOKEN_EXPIRE_SECONDS)
        now = datetime.now()

        # 延长会话信息 Redis TTL
        await RedisCURD(redis).expire(
            key=f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}",
            expire=settings.REFRESH_TOKEN_EXPIRE_SECONDS,
        )

        access_token = create_access_token(
            payload=JWTPayloadSchema(
                sub=session_id,
                is_refresh=False,
                exp=now + access_expires,
                jti=secrets.token_urlsafe(16),
            )
        )

        refresh_token_new = create_access_token(
            payload=JWTPayloadSchema(
                sub=session_id,
                is_refresh=True,
                exp=now + refresh_expires,
                jti=secrets.token_urlsafe(16),
            )
        )

        await RedisCURD(redis).set(
            key=f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}",
            value=access_token,
            expire=settings.ACCESS_TOKEN_EXPIRE_SECONDS,
        )

        await RedisCURD(redis).set(
            key=refresh_key,
            value=refresh_token_new,
            expire=settings.REFRESH_TOKEN_EXPIRE_SECONDS,
        )

        return JWTOutSchema(
            access_token=access_token,
            refresh_token=refresh_token_new,
            token_type=settings.TOKEN_TYPE,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_SECONDS,
        )

    @staticmethod
    async def logout(redis: Redis, session_id: str) -> bool:
        """仅注销当前请求认证的会话。"""
        if not session_id:
            raise CustomException(msg="非法凭证,无法获取会话编号")

        await RedisCURD(redis).delete(f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}")
        await RedisCURD(redis).delete(f"{RedisInitKeyConfig.REFRESH_TOKEN.key}:{session_id}")
        await RedisCURD(redis).delete(f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}")

        logger.info(f"用户退出登录成功,会话编号:{session_id}")

        return True


class CaptchaService:
    """验证码服务。"""

    @staticmethod
    async def get_captcha(redis: Redis) -> CaptchaOutSchema:
        """为已启用或自适应验证码流程生成验证挑战。

        全局开关关闭时仍允许调用，以便登录页在连续失败后展示验证码。
        """

        captcha_base64, captcha_value = CaptchaUtil.captcha_arithmetic()
        captcha_key = get_random_character()

        redis_key = f"{RedisInitKeyConfig.CAPTCHA_CODES.key}:{captcha_key}"
        await RedisCURD(redis).set(
            key=redis_key,
            value=captcha_value,
            expire=settings.CAPTCHA_EXPIRE_SECONDS,
        )

        return CaptchaOutSchema(
            enable=settings.CAPTCHA_ENABLE,
            key=CaptchaKey(captcha_key),
            img_base=CaptchaBase64(f"data:image/png;base64,{captcha_base64}"),
        )

    @staticmethod
    async def check_captcha(redis: Redis, key: str, captcha: str) -> bool:
        """校验验证码。"""
        if not captcha:
            raise CustomException(msg="验证码不能为空")

        redis_key = f"{RedisInitKeyConfig.CAPTCHA_CODES.key}:{key}"
        captcha_value = await RedisCURD(redis).get(redis_key)
        if not captcha_value:
            raise CustomException(msg="验证码已过期")

        if captcha.lower() != captcha_value.lower():
            raise CustomException(msg="验证码错误")

        await RedisCURD(redis).delete(redis_key)
        return True


class AutoLoginService:
    """免登录服务。"""

    AUTO_LOGIN_PREFIX = "fastcore:auto_login:"
    TOKEN_EXPIRE = 300

    @classmethod
    async def get_auto_login_users(cls, db: AsyncSession) -> list[AutoLoginUserSchema]:
        """获取免登录用户列表。"""
        from sqlalchemy import select

        from app.api.v1.module_system.user.model import UserModel

        stmt = select(UserModel).where(UserModel.status == 0)
        stmt = stmt.order_by(UserModel.id)
        result = await db.execute(stmt)
        users = result.scalars().all()

        return [
            AutoLoginUserSchema(
                id=user.id,
                username=user.username,
                name=user.name,
                avatar=user.avatar,
            )
            for user in users
        ]

    @classmethod
    async def create_auto_login_token(
        cls,
        redis: Redis,
        db: AsyncSession,
        user_id: int,
    ) -> AutoLoginTokenSchema:
        """创建免登录Token。"""
        from sqlalchemy import select

        from app.api.v1.module_system.user.model import UserModel

        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise CustomException(msg="用户不存在")

        if user.status == 1:
            raise CustomException(msg="用户已被停用")

        import uuid

        token = str(uuid.uuid4())
        token_key = f"{cls.AUTO_LOGIN_PREFIX}{token}"

        token_data = {
            "user_id": user.id,
            "username": user.username,
            "credential_fingerprint": credential_fingerprint(user.id, user.password),
            "created_at": datetime.now().isoformat(),
        }
        await RedisCURD(redis).set(
            key=token_key,
            value=json.dumps(token_data),
            expire=cls.TOKEN_EXPIRE,
        )

        logger.info(f"创建免登录Token成功,用户:{user.username}")

        return AutoLoginTokenSchema(
            token=token,
            user=AutoLoginUserSchema(
                id=user.id,
                username=user.username,
                name=user.name,
                avatar=user.avatar,
            ),
        )

    @classmethod
    async def auto_login(
        cls,
        request: Request,
        redis: Redis,
        db: AsyncSession,
        token: str,
    ) -> JWTOutSchema:
        """免登录。"""
        from sqlalchemy import select

        from app.api.v1.module_system.user.model import UserModel

        token_key = f"{cls.AUTO_LOGIN_PREFIX}{token}"
        token_data_str = await RedisCURD(redis).getdel(token_key)

        if not token_data_str:
            raise CustomException(msg="免登录Token已过期或无效")

        if isinstance(token_data_str, bytes):
            token_data_str = token_data_str.decode("utf-8")

        token_data = json.loads(token_data_str)
        user_id = token_data.get("user_id")

        stmt = select(UserModel).where(UserModel.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            raise CustomException(msg="用户不存在")

        if user.status == 1:
            raise CustomException(msg="用户已被停用")

        verify_session_credentials(token_data, user.id, user.password)
        await RedisCURD(redis).delete(token_key)

        jwt_token = await LoginService.create_token(request=request, redis=redis, user=user, login_type="PC端")

        logger.info(f"用户{user.username}免登录成功")

        return jwt_token
