import json
from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import Any

from fastapi import Depends, Request
from redis.asyncio.client import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.enums import RedisInitKeyConfig
from app.config.setting import settings
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.core.redis_crud import RedisCURD
from app.core.request_context import RequestContext
from app.core.security import OAuth2Schema, decode_access_token, verify_session_credentials


def _write_request_transaction(method: str) -> bool:
    return method.upper() in {"POST", "PUT", "PATCH", "DELETE"}


async def db_getter(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """在依赖退出前提交写请求、回滚读请求或失败请求，并关闭会话。"""
    async with async_db_session() as session:
        try:
            yield session
            if _write_request_transaction(request.method):
                await session.commit()
            else:
                await session.rollback()
        except BaseException:
            await session.rollback()
            raise


async def redis_getter(request: Request) -> Redis:
    """获取Redis连接。

    Args:
        request (Request): 请求对象

    Returns:
        Redis: Redis连接
    """
    return request.app.state.redis


async def _decode_token_info(token: str, redis: Redis) -> tuple[dict, str]:
    """解码 JWT token 返回 (user_info, session_id)。

    JWT sub 现为纯 session_id，完整会话信息从 Redis 读取。

    Args:
        token: JWT token 字符串
        redis: Redis 连接

    Returns:
        (user_info, session_id): 用户信息字典和会话 ID
    """
    payload = decode_access_token(token)
    if not payload or not hasattr(payload, "is_refresh") or payload.is_refresh:
        raise CustomException(msg="非法凭证", code=10401, status_code=401)

    session_id = payload.sub
    raw = await RedisCURD(redis).get(
        f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}"
    )
    if not raw:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)

    user_info = json.loads(raw)
    if not session_id:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)

    return user_info, session_id


async def _check_token_online(redis: Redis, session_id: str) -> None:
    """检查 token 是否在线（Redis 中存在对应 session）。

    Args:
        redis: Redis 连接
        session_id: 会话 ID
    """
    online_ok = await RedisCURD(redis).exists(
        key=f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}"
    )
    if not online_ok:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)


async def _try_sliding_refresh(redis: Redis, session_id: str) -> None:
    """滑动过期续期（仅在 token 剩余不足一半时触发）。

    Args:
        redis: Redis 连接
        session_id: 会话 ID
    """
    if not settings.TOKEN_SLIDING_EXPIRE:
        return

    ttl = await RedisCURD(redis).ttl(
        key=f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}"
    )
    if ttl > 0 and ttl < settings.ACCESS_TOKEN_EXPIRE_MINUTES // 2:
        await RedisCURD(redis).expire(
            key=f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{session_id}",
            expire=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        )
        await RedisCURD(redis).expire(
            key=f"{RedisInitKeyConfig.REFRESH_TOKEN.key}:{session_id}",
            expire=settings.REFRESH_TOKEN_EXPIRE_MINUTES,
        )


def _build_permission_map(user: Any) -> dict[str, int]:
    permissions: dict[str, int] = {}
    for role in getattr(user, "roles", []) or []:
        if getattr(role, "status", None) != 0 or getattr(role, "is_deleted", False):
            continue
        for menu in getattr(role, "menus", []) or []:
            permission = getattr(menu, "permission", None)
            if getattr(menu, "status", None) == 0 and not getattr(menu, "is_deleted", False) and permission:
                permissions[permission] = menu.id
    return permissions


async def _load_user_from_db(db: AsyncSession, username: str | None = None, *, user_id: int | None = None):
    """从数据库加载用户（含角色、菜单等关联的全量预加载）。

    使用原始查询以绕过 CRUDBase 的权限过滤，确保用户认证阶段不受数据权限影响。
    所有关系链均 eager-loaded，调用方可在会话关闭后安全访问对象属性。

    Args:
        db: 数据库会话
        username: 兼容已有按用户名读取的调用方；会话认证必须传 user_id。
        user_id: 会话绑定的用户主键

    Returns:
        UserModel: 已全量加载的用户 ORM 对象
    """
    from app.api.v1.module_system.role.model import RoleModel
    from app.api.v1.module_system.user.model import UserModel

    stmt = (
        select(UserModel)
        .options(
            selectinload(UserModel.roles).selectinload(RoleModel.menus),
            selectinload(UserModel.created_by),
        )
        .where(
            UserModel.id == user_id if user_id is not None else UserModel.username == username,
            UserModel.is_deleted == False,  # noqa: E712
        )
    )
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user:
        raise CustomException(msg="用户不存在", code=10401, status_code=401)
    if user.status == 1:
        raise CustomException(msg="用户已被停用", code=10401, status_code=401)

    return user


async def _load_authenticated_user(user_id: int):
    """使用短只读会话，避免请求回滚导致认证对象属性过期。"""
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)
    async with async_db_session() as auth_db:
        return await _load_user_from_db(auth_db, user_id=user_id)


async def get_current_user(
    request: Request,
    # 在响应和后台审计之前提交或回滚请求事务。
    db: AsyncSession = Depends(db_getter, scope="function"),
    redis: Redis = Depends(redis_getter),
    token: str = Depends(OAuth2Schema),
) -> AuthSchema:
    """获取当前用户。

    用户查询使用独立的只读数据库会话（不参与请求事务，查询完成后立即释放快照），
    返回的 auth.db 指向请求级事务会话供后续写操作使用。

    Args:
        request (Request): 请求对象
        db (AsyncSession): 请求级事务会话
        redis (Redis): Redis连接
        token (str): 访问令牌

    Returns:
        AuthSchema: 认证信息模型
    """
    if not token:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)

    # 处理Bearer token
    if token.startswith("Bearer"):
        token = token.split(" ")[1]

    # 优先使用请求上下文缓存的会话信息（避免重复 Redis 读取）
    ctx = getattr(request.state, "ctx", None)
    cached_user_info = ctx.jwt_user_info if ctx else None

    if cached_user_info:
        user_info = cached_user_info
    else:
        # 降级路径：自行从 Redis 读取会话信息
        user_info, _ = await _decode_token_info(token, redis)

    session_id = user_info.get("session_id")
    if not session_id:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)

    # Redis 在线检查 + 滑动续期
    await _check_token_online(redis, session_id)
    await _try_sliding_refresh(redis, session_id)

    user = await _load_authenticated_user(user_info.get("user_id"))
    verify_session_credentials(user_info, user.id, user.password)
    permission_map = _build_permission_map(user)

    # 设置请求上下文（仅在当前 request 对象上，业务方通过 request.state.ctx 读取）
    request.state.ctx = replace(
        (getattr(request.state, "ctx", None) or RequestContext()),
        user_id=user.id,
        user_username=user.username,
        session_id=session_id,
        session_info=user_info,
    )

    # 返回的 auth.db 指向请求级事务会话，供后续读写操作使用
    auth = AuthSchema(db=db, redis=redis, permission_map=permission_map, check_data_scope=False, session_id=session_id)
    auth.user = user
    return auth


async def _verify_session(session_id: str, db: AsyncSession, redis: Redis) -> AuthSchema:
    """校验在线会话和当前授权。"""
    await _check_token_online(redis, session_id)
    session_raw = await RedisCURD(redis).get(f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}")
    if not session_raw:
        raise CustomException(msg="认证已失效", code=10401, status_code=401)
    if isinstance(session_raw, bytes):
        session_raw = session_raw.decode("utf-8")
    try:
        user_info = json.loads(session_raw)
    except (TypeError, json.JSONDecodeError):
        raise CustomException(msg="认证已失效", code=10401, status_code=401)
    user = await _load_authenticated_user(user_info.get("user_id"))
    verify_session_credentials(user_info, user.id, user.password)
    auth = AuthSchema(db=db, redis=redis, permission_map=_build_permission_map(user), check_data_scope=False, session_id=session_id)
    auth.user = user
    return auth


class AuthPermission:
    """权限验证类。"""

    def __init__(
        self,
        permissions: list[str] | None = None,
        check_data_scope: bool = True,
    ) -> None:
        """初始化权限验证。

        Args:
            permissions (list[str] | None): 权限标识列表。
            check_data_scope (bool): 是否启用严格模式校验。
        """
        self.permissions = permissions or []
        self.check_data_scope = check_data_scope

    async def __call__(self, auth: AuthSchema = Depends(get_current_user)) -> AuthSchema:
        """调用权限验证。

        Args:
            auth (AuthSchema): 认证信息对象。

        Returns:
            AuthSchema: 认证信息对象。
        """
        # 用 model_copy 派生一份带正确 check_data_scope 的新实例（不修改原实例）
        auth = auth.model_copy(update={"check_data_scope": self.check_data_scope})

        # 超级管理员直接通过
        if auth.user and auth.user.is_superuser:
            return auth

        # 无需验证权限
        if not self.permissions:
            return auth

        # 超级管理员权限标识
        if "*" in self.permissions or "*:*:*" in self.permissions:
            return auth

        # 检查用户是否有角色
        if not auth.user or not auth.user.roles:
            raise CustomException(msg="无权限操作", code=10403, status_code=403)

        # 收集角色菜单权限。
        role_perms = auth.permission_map or _build_permission_map(auth.user)

        if not role_perms:
            raise CustomException(msg="无权限操作", code=10403, status_code=403)

        user_permissions = set(role_perms.keys())

        # 权限验证 - 满足任一权限即可
        if not any(perm in user_permissions for perm in self.permissions):
            logger.error(f"用户缺少任何所需的权限: {self.permissions}")
            raise CustomException(msg="无权限操作", code=10403, status_code=403)

        return auth


def require_superadmin(func):
    """装饰器：仅超级管理员可调用 Service 方法。

    自动校验 ``self.auth.user.is_superuser`` 属性，非超管直接抛出 403。
    适用于实例方法（``Service(auth).xxx(...)``），由 ``self.auth`` 取认证上下文。

    用法:
        class XxxService:
            def __init__(self, auth: AuthSchema) -> None:
                self.auth = auth

            @require_superadmin
            async def create(self, data: ...) -> ...:
                ...
    """
    from functools import wraps

    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        if not self.auth.user or not self.auth.user.is_superuser:
            raise CustomException(msg="仅平台管理员可操作")
        return await func(self, *args, **kwargs)

    return wrapper
