"""Isolated regressions for startup, migration and operational health boundaries."""

import asyncio
import runpy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from alembic import context
from alembic.config import Config
from fastapi import FastAPI, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from typer.testing import CliRunner

import main
from app import init_app
from app.config import setting
from app.config.path_conf import BASE_DIR
from app.core import database, modules
from app.scripts import migrate, startup_policy


def test_alembic_accepts_url_encoded_synthetic_credentials(monkeypatch):
    url = "mysql+asyncmy://synthetic:encoded%40value%25@localhost/synthetic"
    config = Config(str(BASE_DIR / "alembic.ini"))
    configure = Mock()
    monkeypatch.setattr(setting, "get_settings", lambda: SimpleNamespace(ASYNC_DB_URI=url))
    monkeypatch.setattr(context, "config", config, raising=False)
    monkeypatch.setattr(context, "is_offline_mode", lambda: True)
    monkeypatch.setattr(context, "configure", configure)
    monkeypatch.setattr(context, "begin_transaction", nullcontext)
    monkeypatch.setattr(context, "run_migrations", Mock())
    runpy.run_path(str(BASE_DIR / "app/alembic/env.py"))
    assert configure.call_args.kwargs["url"] == url


@pytest.fixture
def lifecycle_resources(monkeypatch):
    app = FastAPI()
    app.state.redis = object()
    events = AsyncMock()
    engine = SimpleNamespace(dispose=AsyncMock())
    hooks = AsyncMock()
    monkeypatch.setattr(init_app, "import_modules_async", events)
    monkeypatch.setattr(init_app, "bootstrap_database", AsyncMock())
    monkeypatch.setattr(init_app, "run_module_hooks", hooks)
    monkeypatch.setattr(init_app.cache_util, "init", AsyncMock())
    monkeypatch.setattr(init_app.cache_util, "clear", AsyncMock())
    monkeypatch.setattr(init_app.FastAPILimiter, "init", AsyncMock())
    monkeypatch.setattr(init_app.FastAPILimiter, "close", AsyncMock())
    monkeypatch.setattr(database, "async_engine", engine)
    monkeypatch.setattr(init_app, "console_start", Mock())
    monkeypatch.setattr(init_app, "console_end", Mock())
    return app, events, engine, hooks


@pytest.mark.parametrize("stage", ["bootstrap", "module", "cache", "limiter", "cancel"])
async def test_failed_startup_releases_resources_and_preserves_failure(monkeypatch, lifecycle_resources, stage):
    app, events, engine, hooks = lifecycle_resources
    failure = asyncio.CancelledError() if stage == "cancel" else RuntimeError("synthetic startup failure")
    if stage == "bootstrap":
        init_app.bootstrap_database.side_effect = failure
    elif stage == "module":
        hooks.side_effect = [failure, None]
    elif stage in ("cache", "cancel"):
        init_app.cache_util.init.side_effect = failure
    else:
        init_app.FastAPILimiter.init.side_effect = failure
    with pytest.raises(asyncio.CancelledError if stage == "cancel" else SystemExit) as caught:
        async with init_app.lifespan(app):
            pytest.fail("failed startup must not serve requests")
    assert caught.value is failure or caught.value.__cause__ is failure
    assert any(call.kwargs.get("status") is False for call in events.await_args_list)
    engine.dispose.assert_awaited_once()
    if stage != "bootstrap":
        assert hooks.await_args.kwargs == {"shutdown": True}
    if stage in ("cache", "limiter", "cancel"):
        init_app.cache_util.clear.assert_awaited_once()
    if stage == "limiter":
        init_app.FastAPILimiter.close.assert_awaited_once()


@pytest.mark.parametrize("body_failure", [False, True])
async def test_shutdown_failures_do_not_skip_remaining_resources(lifecycle_resources, body_failure):
    app, events, engine, hooks = lifecycle_resources
    hooks.side_effect = [None, RuntimeError("synthetic hook failure")]
    init_app.cache_util.clear.side_effect = RuntimeError("synthetic cache failure")
    failure = ValueError("request body failure")
    expected = ValueError if body_failure else ExceptionGroup
    with pytest.raises(expected) as caught:
        async with init_app.lifespan(app):
            if body_failure:
                raise failure
    if body_failure:
        assert caught.value is failure
    init_app.cache_util.clear.assert_awaited_once()
    init_app.FastAPILimiter.close.assert_awaited_once()
    assert events.await_args.kwargs["status"] is False
    engine.dispose.assert_awaited_once()


async def test_all_module_shutdown_hooks_run_after_a_hook_fails(monkeypatch):
    stopped = []

    async def stop(entry):
        stopped.append(entry)
        if entry == "two:fail":
            raise RuntimeError("synthetic hook failure")

    monkeypatch.setattr(
        modules, "get_modules",
        lambda: (
            modules.ModuleSpec(name="one", shutdown_hooks=("one:stop",)),
            modules.ModuleSpec(name="two", shutdown_hooks=("two:fail", "two:stop")),
        ),
    )
    monkeypatch.setattr(modules, "resolve_entrypoint", lambda entry: lambda: stop(entry))
    with pytest.raises(ExceptionGroup):
        await modules.run_module_hooks(shutdown=True)
    assert stopped == ["two:fail", "two:stop", "one:stop"]


def test_migration_cli_runs_only_migrations_under_the_startup_lock(monkeypatch):
    command = "migrate"
    redis = SimpleNamespace(ping=AsyncMock(), aclose=AsyncMock())
    locked = False
    commands = []

    async def acquire(*args, **kwargs):
        nonlocal locked
        assert not locked, "migration must acquire the startup lock exactly once"
        locked = True
        return True, "synthetic-owner"

    async def unlock(*args, **kwargs):
        nonlocal locked
        locked = False
        return True

    async def spawn(*args, **kwargs):
        assert locked, "migration process must be protected by the startup lock"
        commands.append(args)
        return SimpleNamespace(wait=AsyncMock(return_value=0))

    monkeypatch.setattr(init_app.RedisCURD, "lock", acquire)
    monkeypatch.setattr(init_app.RedisCURD, "unlock", unlock)
    monkeypatch.setattr(init_app.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(migrate, "upgrade_database", Mock(side_effect=AssertionError("unlocked migration")))
    with patch("redis.asyncio.Redis.from_url", return_value=redis):
        result = CliRunner().invoke(main.fastcore_cli, [command, "--env=dev"])
    assert result.exit_code == 0, result.exception
    assert len(commands) == 1
    assert "--migrate-only" in commands[0]
    assert commands[0][-1] == "--migrate-only"
    assert not locked
    redis.ping.assert_awaited_once()
    redis.aclose.assert_awaited_once()
    migrate.upgrade_database.assert_not_called()


async def test_migration_child_does_not_reacquire_lock_or_reseed(monkeypatch):
    upgrade = AsyncMock()
    dispose = AsyncMock()
    monkeypatch.setattr(init_app, "run_startup_migration", upgrade)
    monkeypatch.setattr(database, "async_engine", SimpleNamespace(dispose=dispose))
    monkeypatch.setattr(init_app, "_startup_schema_lock", Mock(side_effect=AssertionError("nested lock")))
    from app.scripts.initialize import InitializeData

    seed = AsyncMock(side_effect=AssertionError("migration must not replay seeds"))
    monkeypatch.setattr(InitializeData, "init_db", seed)
    monkeypatch.setattr(startup_policy, "validate_database_schema", AsyncMock(side_effect=AssertionError("migration must not bootstrap")))
    await startup_policy._bootstrap(migrate_only=True)
    upgrade.assert_awaited_once()
    seed.assert_not_awaited()
    dispose.assert_awaited_once()


@pytest.mark.parametrize("redis_available", [False, True])
async def test_health_probes_bypass_business_limits_and_report_redis_failure(monkeypatch, tmp_path, redis_available):
    from app.api.v1.module_common.monitoring import health

    async def limited(self, request: Request, response: Response):
        raise HTTPException(status_code=429)

    monkeypatch.setattr(init_app.RateLimiter, "__call__", limited)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'health.db'}")
    monkeypatch.setattr(health, "async_db_session", async_sessionmaker(engine))
    redis = SimpleNamespace(ping=AsyncMock(side_effect=None if redis_available else ConnectionError("synthetic Redis outage")))
    app = FastAPI()
    app.state.redis = redis
    init_app.register_routers(app)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for _ in range(3):
                assert (await client.get("/common/health/live")).status_code == 200
            redis.ping.assert_not_awaited()
            response = await client.get("/common/health/ready")
            assert response.status_code == (200 if redis_available else 503)
            assert response.json()["data"]["dependencies"]["redis"]["status"] == int(redis_available)
            assert (await client.get("/system/auth/captcha/get")).status_code == 429
    finally:
        await engine.dispose()
