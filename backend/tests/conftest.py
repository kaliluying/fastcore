"""
conftest — 模块化 API 接口测试共享 fixture。

提供:
- test_client: FastAPI TestClient 实例 (session 级复用)
- assert_route: 验证接口返回明确的预期状态码，异常直接失败
"""

import os
import sys
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient

# ============================================================
# 测试环境变量
# ============================================================

_TEST_DB_PATH = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name

os.environ["DATABASE_TYPE"] = "sqlite"
os.environ["DATABASE_NAME"] = _TEST_DB_PATH
os.environ["REDIS_ENABLE"] = "true"
os.environ["SECRET_KEY"] = "test-secret-key-for-backend-tests-32-chars"
os.environ["POOL_SIZE"] = "1"
os.environ["MAX_OVERFLOW"] = "1"

from app.config.setting import settings

settings.DATABASE_TYPE = "sqlite"
settings.DATABASE_NAME = _TEST_DB_PATH
settings.REDIS_ENABLE = True
settings.SECRET_KEY = "test-secret-key-for-backend-tests-32-chars"
settings.POOL_SIZE = 1
settings.MAX_OVERFLOW = 1
settings.CAPTCHA_ENABLE = False  # 测试环境关闭验证码

# ============================================================
# Mock Redis — dict 存储，支持 get/set/delete/exists/keys/ttl/expire
# 登录成功后写入的 session 数据可在后续请求中正确读取
# ============================================================

_mock_redis_store: dict[bytes, bytes] = {}


def _redis_get(name: bytes) -> bytes | None:
    return _mock_redis_store.get(name)


async def _redis_set(name: bytes, value: bytes, ex: int | None = None, nx: bool = False) -> bool | None:
    if nx and name in _mock_redis_store:
        return None
    _mock_redis_store[name] = value
    return True


async def _redis_incr(name: bytes) -> int:
    """Increment a mock Redis counter using the same byte representation as Redis."""
    current = _mock_redis_store.get(name, b"0")
    if isinstance(current, bytes):
        current = current.decode("utf-8")
    value = int(current) + 1
    _mock_redis_store[name] = str(value).encode("utf-8")
    return value


async def _redis_getdel(name: bytes) -> bytes | None:
    """Atomically read and remove one mock Redis value."""
    return _mock_redis_store.pop(name, None)


async def _redis_delete(*names: bytes) -> int:
    count = 0
    for n in names:
        if _mock_redis_store.pop(n, None) is not None:
            count += 1
    return count


def _redis_keys(pattern: bytes | None = None) -> list[bytes]:
    if pattern == b"*" or pattern is None:
        return list(_mock_redis_store.keys())
    return [k for k in _mock_redis_store if pattern == b"*" or k.startswith(pattern.replace(b"*", b""))]


def _redis_exists(*names: bytes) -> int:
    return sum(1 for n in names if n in _mock_redis_store)


def _redis_ttl(name: bytes) -> int:
    return 3600 if name in _mock_redis_store else -2


async def _redis_expire(name: bytes, time: int) -> bool:
    return name in _mock_redis_store


async def _redis_flushall(asynchronous: bool = False) -> bool:
    _mock_redis_store.clear()
    return True


async def _redis_flushdb(asynchronous: bool = False) -> bool:
    _mock_redis_store.clear()
    return True


async def _redis_close() -> None:
    pass


async def _redis_aclose() -> None:
    pass


async def _redis_hmget(name: bytes, keys: list[bytes]) -> list[bytes | None]:
    return [_mock_redis_store.get(name + b":" + k) for k in keys]


async def _redis_hset(name: bytes, key: bytes, value: bytes) -> int:
    _mock_redis_store[name + b":" + key] = value
    return 1


async def _redis_hgetall(name: bytes) -> dict[bytes, bytes]:
    prefix = name + b":"
    return {k[len(prefix) :]: v for k, v in _mock_redis_store.items() if k.startswith(prefix)}


async def _redis_hdel(name: bytes, *keys: bytes) -> int:
    count = 0
    for k in keys:
        if _mock_redis_store.pop(name + b":" + k, None) is not None:
            count += 1
    return count


def _redis_info(section: str | None = None) -> dict:
    return {}


def _redis_dbsize() -> int:
    return len(_mock_redis_store)


_mock_redis = AsyncMock()
_mock_redis.ping = AsyncMock(return_value=True)
_mock_redis.get = AsyncMock(side_effect=_redis_get)
_mock_redis.set = AsyncMock(side_effect=_redis_set)
_mock_redis.incr = AsyncMock(side_effect=_redis_incr)
_mock_redis.getdel = AsyncMock(side_effect=_redis_getdel)
_mock_redis.delete = AsyncMock(side_effect=_redis_delete)
_mock_redis.keys = AsyncMock(side_effect=_redis_keys)
_mock_redis.exists = AsyncMock(side_effect=_redis_exists)
_mock_redis.ttl = AsyncMock(side_effect=_redis_ttl)
_mock_redis.expire = AsyncMock(side_effect=_redis_expire)
_mock_redis.flushall = AsyncMock(side_effect=_redis_flushall)
_mock_redis.flushdb = AsyncMock(side_effect=_redis_flushdb)
_mock_redis.close = AsyncMock(side_effect=_redis_close)
_mock_redis.aclose = AsyncMock(side_effect=_redis_aclose)
_mock_redis.hmget = AsyncMock(side_effect=_redis_hmget)
_mock_redis.hset = AsyncMock(side_effect=_redis_hset)
_mock_redis.hgetall = AsyncMock(side_effect=_redis_hgetall)
_mock_redis.hdel = AsyncMock(side_effect=_redis_hdel)
_mock_redis.info = AsyncMock(side_effect=_redis_info)
_mock_redis.dbsize = AsyncMock(side_effect=_redis_dbsize)

patch("redis.asyncio.Redis.from_url", return_value=_mock_redis).start()
patch("app.init_app.FastAPILimiter.init", new=AsyncMock()).start()
patch("app.init_app.FastAPILimiter.close", new=AsyncMock()).start()

# RateLimiter → no-op（需匹配 FastAPI 依赖注入签名）
from fastapi_limiter.depends import RateLimiter
from starlette.requests import Request
from starlette.responses import Response


async def _noop_rate_limit(request: Request, response: Response) -> None:
    pass


RateLimiter.__call__ = _noop_rate_limit

# ============================================================
# 精简 lifespan — 仅做数据库初始化
# ============================================================


async def _initialize_test_app(app) -> None:
    """Initialize database state shared by a test application.

    Args:
        app: FastAPI application whose Redis state should be initialized.
    """
    from app.init_app import run_startup_migration
    from app.scripts.initialize import InitializeData

    await run_startup_migration()
    await InitializeData().init_db()
    app.state.redis = _mock_redis

    # 将 admin 密码重置为已知密码 "admin123"
    from sqlalchemy import update

    from app.api.v1.module_system.user.model import UserModel
    from app.core.database import async_db_session
    from app.utils.hash_bcrpy_util import PwdUtil

    async with async_db_session() as db:
        await db.execute(update(UserModel).where(UserModel.username == "admin").values(password=PwdUtil.hash_password("admin123")))
        await db.commit()


@asynccontextmanager
async def _test_lifespan(app) -> AsyncGenerator[Any, None]:
    """Start the test application with all core modules."""
    await _initialize_test_app(app)

    yield


from main import create_app

_app = create_app()
_app.router.lifespan_context = _test_lifespan

_ai_app = create_app()
_ai_app.router.lifespan_context = _test_lifespan

# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(scope="session")
def _api_client() -> TestClient:
    """session 级共享 TestClient，所有测试复用同一个 app 实例。"""
    with TestClient(_app) as c:
        yield c


@pytest.fixture(scope="session")
def ai_client() -> TestClient:
    """session 级 AI 客户端，仅供 AI 模块接口测试使用。"""
    with TestClient(_ai_app) as c:
        yield c


@pytest.fixture
def test_client(_api_client: TestClient) -> TestClient:
    """每个测试函数获取同一个 session 级 TestClient 的引用。"""
    return _api_client


@pytest.fixture
def auth_headers(_api_client: TestClient) -> dict[str, str]:
    """每个测试独立登录，避免退出登录或刷新令牌污染其他测试。"""
    resp = _api_client.post(
        "/system/auth/login",
        data={"username": "admin", "password": "admin123"},
    )
    assert resp.status_code == 200, f"admin 登录失败: {resp.text}"
    token = resp.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ============================================================
# 公共辅助函数
# ============================================================


def assert_route(
    test_client: TestClient,
    method: str,
    path: str,
    *,
    expected_status: int = 200,
    auth: dict[str, str] | None = None,
    **kwargs,
) -> None:
    """断言接口路由存在且返回码符合预期。

    Args:
        test_client: FastAPI TestClient 实例。
        method: HTTP 方法 (GET/POST/PUT/DELETE 等)。
        path: 接口路径。
        expected_status: 期望的 HTTP 状态码，默认要求成功 (200)。
        auth: 认证 headers（dict），传入则合并到请求头。
        **kwargs: 传递给 TestClient 请求方法的额外参数 (json/data/params/headers 等)。
    """
    headers: dict[str, str] = {}
    if auth:
        headers.update(auth)
    if "headers" in kwargs:
        headers.update(kwargs.pop("headers"))
    if headers:
        kwargs["headers"] = headers

    response = test_client.request(method, path, **kwargs)
    assert response.status_code == expected_status, f"{method} {path} 期望 {expected_status}，实际 {response.status_code}"
