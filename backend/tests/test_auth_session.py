import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.module_system.auth import oauth_service
from app.api.v1.module_system.auth.service import AutoLoginService, LoginService
from app.common.enums import RedisInitKeyConfig
from app.core import dependencies
from app.core.base_schema import JWTOutSchema, RefreshTokenPayloadSchema
from app.core.exceptions import CustomException
from app.core.security import credential_fingerprint, decode_access_token


@pytest.mark.parametrize("cached", [False, True])
async def test_http_auth_preserves_verified_login_session(monkeypatch, cached):
    from sqlalchemy.ext.asyncio import AsyncSession

    user_info = {"user_id": 7, "user_name": "admin", "session_id": "verified-http-session", "credential_fingerprint": credential_fingerprint(7, "fixture")}
    state = SimpleNamespace()
    if cached:
        state.ctx = dependencies.RequestContext(jwt_user_info=user_info)
    request = SimpleNamespace(state=state)
    redis = FakeRedis()
    user = SimpleNamespace(id=7, username="admin", password="fixture")
    decode = AsyncMock(return_value=(user_info, user_info["session_id"]))
    online = AsyncMock()
    monkeypatch.setattr(dependencies, "_decode_token_info", decode)
    monkeypatch.setattr(dependencies, "_check_token_online", online)
    monkeypatch.setattr(dependencies, "_try_sliding_refresh", AsyncMock())
    load_user = AsyncMock(return_value=user)
    monkeypatch.setattr(dependencies, "_load_user_from_db", load_user)
    async with AsyncSession() as db:
        auth = await dependencies.get_current_user(request=request, db=db, redis=redis, token="synthetic-http-token")
        assert auth.session_id == user_info["session_id"]
        assert request.state.ctx.session_id == auth.session_id
        assert "session_id" not in auth.model_dump()
        online.assert_awaited_once_with(redis, auth.session_id)
        assert decode.await_count == (0 if cached else 1)
        assert load_user.await_args.kwargs == {"user_id": 7}


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.expires: dict[str, int | None] = {}

    async def set(self, name: str, value: bytes, ex: int | None = None, **_: object) -> bool:
        self.values[name] = value
        self.expires[name] = ex
        return True

    async def get(self, name: str) -> bytes | None:
        return self.values.get(name)

    async def getdel(self, name: str) -> bytes | None:
        return self.values.pop(name, None)

    async def delete(self, *names: str) -> int:
        return sum(self.values.pop(name, None) is not None for name in names)

    async def exists(self, *names: str) -> int:
        return sum(name in self.values for name in names)

    async def incr(self, name: str) -> int:
        current = self.values.get(name, b"0")
        value = int(current.decode("utf-8") if isinstance(current, bytes) else current) + 1
        self.values[name] = str(value).encode("utf-8")
        return value

    async def expire(self, name: str, seconds: int) -> bool:
        if name not in self.values:
            return False
        self.expires[name] = seconds
        return True

    async def eval(self, _script: str, _numkeys: int, key: str, expected: str) -> int:
        """Minimal atomic compare-and-delete behavior for refresh tests."""
        current = self.values.get(key)
        if current is None:
            return 0
        current_value = current.decode("utf-8") if isinstance(current, bytes) else str(current)
        if current_value != expected:
            return 0
        self.values.pop(key, None)
        return 1


@pytest.mark.asyncio
async def test_create_token_stores_user_session() -> None:
    request = SimpleNamespace(
        headers={"user-agent": "pytest"},
        client=SimpleNamespace(host="127.0.0.1"),
        state=SimpleNamespace(),
    )
    redis = FakeRedis()
    user = SimpleNamespace(id=7, username="admin", password="fixture")

    token = await LoginService.create_token(
        request=request,  # type: ignore[arg-type]
        redis=redis,  # type: ignore[arg-type]
        user=user,  # type: ignore[arg-type]
        login_type="PC端",
    )

    session_id = decode_access_token(token.access_token).sub
    session_key = f"{RedisInitKeyConfig.USER_SESSION.key}:{session_id}"

    assert session_key in redis.values
    session = json.loads(redis.values[session_key].decode("utf-8"))
    assert session["session_id"] == session_id
    assert session["user_id"] == 7
    assert session["username"] == "admin"
    assert session["user_name"] == "admin"
    assert "tenant_id" not in session


@pytest.mark.asyncio
async def test_logout_revokes_only_the_authenticated_session() -> None:
    """注销一个会话时，其他设备的访问状态必须保持有效。"""
    request = SimpleNamespace(
        headers={"user-agent": "pytest"},
        client=SimpleNamespace(host="127.0.0.1"),
        state=SimpleNamespace(),
    )
    redis = FakeRedis()
    user = SimpleNamespace(id=7, username="admin", password="fixture")

    first = await LoginService.create_token(request=request, redis=redis, user=user, login_type="PC端")
    second = await LoginService.create_token(request=request, redis=redis, user=user, login_type="移动端")
    first_session_id = decode_access_token(first.access_token).sub
    second_session_id = decode_access_token(second.access_token).sub

    await LoginService.logout(redis=redis, session_id=first_session_id)

    assert f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{first_session_id}" not in redis.values
    assert f"{RedisInitKeyConfig.USER_SESSION.key}:{first_session_id}" not in redis.values
    assert f"{RedisInitKeyConfig.ACCESS_TOKEN.key}:{second_session_id}" in redis.values
    assert f"{RedisInitKeyConfig.USER_SESSION.key}:{second_session_id}" in redis.values


@pytest.mark.asyncio
async def test_refresh_token_is_rotated_atomically(monkeypatch) -> None:
    """The same refresh token can rotate a session only once."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.api.v1.module_system.user.service import UserCRUD

    request = SimpleNamespace(
        headers={"user-agent": "pytest"},
        client=SimpleNamespace(host="127.0.0.1"),
        state=SimpleNamespace(),
    )
    redis = FakeRedis()
    user = SimpleNamespace(id=7, username="admin", password="fixture", status=0)
    initial = await LoginService.create_token(request=request, redis=redis, user=user, login_type="PC端")

    async def load_user(self, **_: object):
        return user

    monkeypatch.setattr(UserCRUD, "get", load_user)
    db = AsyncSession()
    try:
        rotated = await LoginService.refresh_token(
            db=db,
            redis=redis,
            refresh_token=RefreshTokenPayloadSchema(refresh_token=initial.refresh_token),
        )
        assert rotated.refresh_token != initial.refresh_token
        with pytest.raises(CustomException, match="刷新令牌已失效"):
            await LoginService.refresh_token(
                db=db,
                redis=redis,
                refresh_token=RefreshTokenPayloadSchema(refresh_token=initial.refresh_token),
            )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_failed_logins_escalate_to_captcha_and_account_lock(monkeypatch) -> None:
    """Repeated failures must trigger a challenge and then a temporary lock."""
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.api.v1.module_system.auth import service as auth_service
    from app.config.setting import settings

    request = SimpleNamespace(
        headers={"user-agent": "pytest"},
        client=SimpleNamespace(host="127.0.0.1"),
        state=SimpleNamespace(),
    )
    redis = FakeRedis()
    login_form = SimpleNamespace(
        username="unknown-security-test-user",
        password="wrong",
        login_type="PC端",
        captcha_key=None,
        captcha=None,
    )

    async def missing_user(self, **_: object):
        return None

    async def no_login_log(**_: object) -> None:
        return None

    monkeypatch.setattr(auth_service.UserCRUD, "get", missing_user)
    monkeypatch.setattr(auth_service, "_write_login_log", no_login_log)
    async def no_location(_ip: str | None) -> str:
        return ""

    monkeypatch.setattr(auth_service.IpLocalUtil, "resolve_location_for_log", staticmethod(no_location))
    monkeypatch.setattr(settings, "CAPTCHA_ENABLE", False)
    db = AsyncSession()

    try:
        for _ in range(settings.LOGIN_CAPTCHA_AFTER_FAILURES):
            with pytest.raises(CustomException) as error:
                await LoginService.authenticate_user(request, redis, login_form, db)
            assert error.value.status_code == 401

        with pytest.raises(CustomException) as captcha_error:
            await LoginService.authenticate_user(request, redis, login_form, db)
        assert captcha_error.value.status_code == 400
        assert captcha_error.value.msg == "验证码不能为空"

        with pytest.raises(CustomException) as lock_error:
            await LoginService.authenticate_user(request, redis, login_form, db)
        assert lock_error.value.status_code == 429
        assert lock_error.value.msg == "验证码不能为空"

        with pytest.raises(CustomException) as locked_error:
            await LoginService.authenticate_user(request, redis, login_form, db)
        assert locked_error.value.status_code == 429
        assert locked_error.value.msg == "登录失败次数过多，请稍后再试"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_oauth_ticket_is_single_use() -> None:
    """OAuth redirects expose an opaque ticket that can be exchanged once."""
    redis = FakeRedis()
    token = JWTOutSchema(
        access_token="access-token",
        refresh_token="refresh-token",
        expires_in=60,
    )
    ticket = await oauth_service.save_oauth_ticket(redis, token)

    exchanged = await oauth_service.exchange_oauth_ticket(redis, ticket)
    assert exchanged == token
    with pytest.raises(CustomException) as replay_error:
        await oauth_service.exchange_oauth_ticket(redis, ticket)
    assert replay_error.value.status_code == 401


@pytest.mark.asyncio
async def test_auto_login_token_is_consumed_before_session_creation() -> None:
    """An auto-login token must not be reusable after the first successful login."""
    token = "auto-login-once"
    redis = FakeRedis()
    redis.values[f"{AutoLoginService.AUTO_LOGIN_PREFIX}{token}"] = json.dumps(
        {"user_id": 7, "username": "admin", "credential_fingerprint": credential_fingerprint(7, "fixture")}
    ).encode("utf-8")

    class FakeResult:
        def scalar_one_or_none(self):
            return SimpleNamespace(id=7, username="admin", password="fixture", status=0)

    class FakeDb:
        async def execute(self, _statement):
            return FakeResult()

    request = SimpleNamespace(
        headers={"user-agent": "pytest"},
        client=SimpleNamespace(host="127.0.0.1"),
        state=SimpleNamespace(),
    )
    first = await AutoLoginService.auto_login(request, redis, FakeDb(), token)

    assert first.access_token
    with pytest.raises(CustomException) as replay_error:
        await AutoLoginService.auto_login(request, redis, FakeDb(), token)
    assert replay_error.value.msg == "免登录Token已过期或无效"


@pytest.mark.asyncio
async def test_missing_user_login_logs_client_details(monkeypatch) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.api.v1.module_system.auth import service as auth_service
    from app.config.setting import settings

    request = SimpleNamespace(
        headers={"user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/120.0.0.0"},
        client=SimpleNamespace(host="203.0.113.195"),
        state=SimpleNamespace(),
    )
    redis = FakeRedis()
    logged_calls = []

    async def record_login_log(**kwargs):
        logged_calls.append(kwargs)

    async def missing_user(self, **_: object):
        return None

    monkeypatch.setattr(auth_service.UserCRUD, "get", missing_user)
    monkeypatch.setattr(auth_service, "_write_login_log", record_login_log)
    monkeypatch.setattr(settings, "CAPTCHA_ENABLE", False)

    login_form = SimpleNamespace(
        username="nonexistent_user",
        password="any_password",
        login_type="PC端",
        captcha_key=None,
        captcha=None,
    )

    with pytest.raises(CustomException) as exc:
        await LoginService.authenticate_user(request, redis, login_form, AsyncSession())

    assert exc.value.status_code == 401
    assert len(logged_calls) == 1
    log_record = logged_calls[0]
    assert log_record["username"] == "nonexistent_user"
    assert log_record["status"] == 2
    assert log_record["login_ip"] == "203.0.113.195"
    assert log_record["msg"] == "账号或密码错误"


@pytest.mark.asyncio
async def test_load_authenticated_user_reuses_request_session(monkeypatch) -> None:
    from app.core import dependencies

    expunged = False
    new_session_called = False

    class FakeSession:
        def expunge_all(self):
            nonlocal expunged
            expunged = True

    async def fake_load_from_db(db, user_id=None):
        return SimpleNamespace(id=user_id, status=0)

    def fake_async_db_session():
        nonlocal new_session_called
        new_session_called = True
        raise AssertionError("should not open new session when db is provided")

    monkeypatch.setattr(dependencies, "_load_user_from_db", fake_load_from_db)
    monkeypatch.setattr(dependencies, "async_db_session", fake_async_db_session)

    session = FakeSession()
    user = await dependencies._load_authenticated_user(42, db=session)
    assert user.id == 42
    assert expunged is True
    assert new_session_called is False


@pytest.mark.asyncio
async def test_request_log_middleware_preserves_custom_exception() -> None:
    from starlette.requests import Request

    from app.core.middlewares import RequestLogMiddleware

    async def failing_app(scope, receive, send):
        raise CustomException(msg="业务自定义拒绝", code=10403, status_code=403)

    middleware = RequestLogMiddleware(failing_app)
    req = Request(scope={
        "type": "http",
        "method": "GET",
        "path": "/api/v1/test",
        "headers": [],
        "query_string": b"",
        "server": ("testserver", 80),
    })

    async def call_next(request):
        raise CustomException(msg="业务自定义拒绝", code=10403, status_code=403)

    response = await middleware.dispatch(req, call_next)
    assert response.status_code == 403
    body = json.loads(response.body.decode("utf-8"))
    assert body["code"] == 10403
    assert body["msg"] == "业务自定义拒绝"
