import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from sqlalchemy import Column, Index, Integer, MetaData, Table, create_engine, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app import init_app
from app.common.enums import EnvironmentEnum
from app.config.path_conf import BASE_DIR
from app.core import database
from app.core.base_model import MappedBase
from app.plugin.module_ai.knowledge import recovery
from app.scripts import startup_policy
from app.scripts.initialize import InitializeData


@pytest.mark.parametrize("environment", [EnvironmentEnum.PROD, EnvironmentEnum.DEV])
async def test_startup_uses_environment_policy(monkeypatch, environment):
    app = FastAPI()
    app.state.redis = object()
    monkeypatch.setattr(init_app.settings, "ENVIRONMENT", environment)
    bootstrap = AsyncMock()
    monkeypatch.setattr(init_app, "bootstrap_database", bootstrap)
    monkeypatch.setattr(init_app, "import_modules_async", AsyncMock())
    monkeypatch.setattr(init_app, "run_module_hooks", AsyncMock())
    monkeypatch.setattr(init_app.cache_util, "init", AsyncMock())
    monkeypatch.setattr(init_app.cache_util, "clear", AsyncMock())
    monkeypatch.setattr(init_app.FastAPILimiter, "init", AsyncMock())
    monkeypatch.setattr(init_app.FastAPILimiter, "close", AsyncMock())
    monkeypatch.setattr(database, "async_engine", SimpleNamespace(dispose=AsyncMock()))
    async with init_app.lifespan(app):
        bootstrap.assert_awaited_once_with(app.state.redis, environment=environment)


async def test_startup_rejects_disabled_redis_before_connecting(monkeypatch):
    monkeypatch.setattr(init_app.settings, "REDIS_ENABLE", False)
    connect = AsyncMock()
    monkeypatch.setattr(init_app, "import_modules_async", connect)
    with pytest.raises(SystemExit):
        async with init_app.lifespan(FastAPI()):
            pytest.fail("disabled Redis must not start the service")
    connect.assert_not_awaited()


@pytest.mark.parametrize("exit_mode", ["shutdown", "body-error", "startup-error", "startup-cancel"])
async def test_recovery_task_is_awaited_before_shutdown_or_failed_startup(monkeypatch, exit_mode):
    app = FastAPI()
    app.state.redis = object()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def polling():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def initialize_ai():
        recovery._worker = asyncio.create_task(polling())
        await started.wait()

    async def dispose():
        assert stopped.is_set()
        assert recovery._worker is None

    monkeypatch.setattr(init_app.settings, "ENVIRONMENT", EnvironmentEnum.PROD)
    monkeypatch.setattr(init_app, "bootstrap_database", AsyncMock())
    monkeypatch.setattr(init_app, "import_modules_async", AsyncMock())
    async def module_hooks(*, shutdown=False):
        if shutdown:
            await recovery.stop_knowledge_recovery()
        else:
            await initialize_ai()

    monkeypatch.setattr(init_app, "run_module_hooks", module_hooks)
    monkeypatch.setattr(init_app, "console_start", lambda **kwargs: None)
    monkeypatch.setattr(init_app, "console_end", lambda: None)
    monkeypatch.setattr(init_app.cache_util, "init", AsyncMock())
    monkeypatch.setattr(init_app.cache_util, "clear", AsyncMock())
    monkeypatch.setattr(init_app.FastAPILimiter, "init", AsyncMock())
    monkeypatch.setattr(init_app.FastAPILimiter, "close", AsyncMock())
    monkeypatch.setattr(recovery, "_worker", None)
    engine = SimpleNamespace(dispose=AsyncMock(side_effect=dispose))
    monkeypatch.setattr(database, "async_engine", engine)
    if exit_mode == "startup-error":
        monkeypatch.setattr(init_app.cache_util, "init", AsyncMock(side_effect=RuntimeError("cache unavailable")))
        expected = pytest.raises(SystemExit)
    elif exit_mode == "startup-cancel":
        monkeypatch.setattr(init_app.cache_util, "init", AsyncMock(side_effect=asyncio.CancelledError))
        expected = pytest.raises(asyncio.CancelledError)
    elif exit_mode == "body-error":
        expected = pytest.raises(RuntimeError, match="body failed")
    else:
        expected = nullcontext()
    with expected:
        async with init_app.lifespan(app):
            assert started.is_set()
            if exit_mode == "body-error":
                raise RuntimeError("body failed")
    assert stopped.is_set()
    assert recovery._worker is None
    if exit_mode in ("shutdown", "body-error"):
        engine.dispose.assert_awaited_once()


def test_http_routes_use_configured_limits(monkeypatch):
    monkeypatch.setattr(init_app.settings, "RATE_LIMITER_TIMES", 7)
    monkeypatch.setattr(init_app.settings, "RATE_LIMITER_SECONDS", 23)
    registrations = []

    class RouterCapture:
        def include_router(self, *args, **kwargs):
            registrations.extend(dependency.dependency for dependency in kwargs["dependencies"])

    init_app.register_routers(RouterCapture())
    assert len(registrations) >= 4
    assert all(isinstance(limiter, init_app.RateLimiter) for limiter in registrations)
    assert all(limiter.times == 7 and limiter.milliseconds == 23000 for limiter in registrations)


@pytest.mark.parametrize("setting_name", ["RATE_LIMITER_TIMES", "RATE_LIMITER_SECONDS"])
def test_nonpositive_limits_fail_before_route_registration(monkeypatch, setting_name):
    monkeypatch.setattr(init_app.settings, setting_name, 0)
    with pytest.raises(ValueError, match="必须为正整数"):
        init_app.register_routers(FastAPI())


async def test_schema_validation_is_read_only_and_rejects_missing_structure(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'schema.db'}")
    monkeypatch.setattr(database, "async_engine", engine)
    try:
        with pytest.raises(RuntimeError, match="迁移版本不匹配"):
            await startup_policy.validate_database_schema()
        async with engine.connect() as connection:
            assert await connection.run_sync(lambda sync: inspect(sync).get_table_names()) == []
        config = Config(str(BASE_DIR / "alembic.ini"))
        config.set_main_option("script_location", str(BASE_DIR / "app" / "alembic"))
        heads = ScriptDirectory.from_config(config).get_heads()
        async with engine.begin() as connection:
            await connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(255) PRIMARY KEY)"))
            for head in heads:
                await connection.execute(text("INSERT INTO alembic_version VALUES (:head)"), {"head": head})
        with pytest.raises(RuntimeError, match="缺少表"):
            await startup_policy.validate_database_schema()
    finally:
        await engine.dispose()


async def test_lease_loss_interrupts_schema_work(monkeypatch):
    renewal_started = asyncio.Event()
    body_cancelled = asyncio.Event()
    sleep = asyncio.sleep

    async def fast_renewal_sleep(seconds):
        if seconds == 30:
            renewal_started.set()
            await sleep(0)
        else:
            await sleep(seconds)

    monkeypatch.setattr(init_app.asyncio, "sleep", fast_renewal_sleep)
    monkeypatch.setattr(init_app.RedisCURD, "lock", AsyncMock(return_value=(True, "owner")))
    monkeypatch.setattr(init_app.RedisCURD, "renew_lock", AsyncMock(return_value=False))
    unlock = AsyncMock(return_value=True)
    monkeypatch.setattr(init_app.RedisCURD, "unlock", unlock)
    with pytest.raises(RuntimeError, match="启动锁已失效"):
        async with init_app._startup_schema_lock(object()):
            try:
                await renewal_started.wait()
                await asyncio.Event().wait()
            finally:
                body_cancelled.set()
    assert body_cancelled.is_set()
    unlock.assert_awaited_once()


@pytest.mark.parametrize("cause", ["cancel", "lease"])
async def test_cancelling_bootstrap_terminates_the_actual_process(monkeypatch, tmp_path, cause):
    marker = tmp_path / "continued-writing"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time,pathlib,sys; time.sleep(10); pathlib.Path(sys.argv[1]).write_text('unsafe')",
        str(marker),
    )

    @asynccontextmanager
    async def unlocked(redis):
        yield

    if cause == "cancel":
        monkeypatch.setattr(init_app, "_startup_schema_lock", unlocked)
    else:
        sleep = asyncio.sleep

        async def fast_renewal_sleep(seconds):
            await sleep(0.05 if seconds == 30 else seconds)

        monkeypatch.setattr(init_app.asyncio, "sleep", fast_renewal_sleep)
        monkeypatch.setattr(init_app.RedisCURD, "lock", AsyncMock(return_value=(True, "owner")))
        monkeypatch.setattr(init_app.RedisCURD, "renew_lock", AsyncMock(return_value=False))
        monkeypatch.setattr(init_app.RedisCURD, "unlock", AsyncMock(return_value=True))
    monkeypatch.setattr(init_app.asyncio, "create_subprocess_exec", AsyncMock(return_value=process))
    task = asyncio.create_task(init_app.bootstrap_database(object()))
    if cause == "cancel":
        await asyncio.sleep(0.05)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cause == "cancel" else RuntimeError):
        await task
    assert process.returncode is not None
    assert not marker.exists()


@pytest.mark.parametrize("missing", ["column", "index"])
async def test_schema_validation_rejects_drift_without_repairing_it(monkeypatch, tmp_path, missing):
    metadata = MetaData()
    table = Table("schema_probe", metadata, Column("id", Integer, primary_key=True), Column("value", Integer))
    required_index = Index("ix_schema_probe_value", table.c.value)
    monkeypatch.setattr(MappedBase, "metadata", metadata)
    monkeypatch.setattr(InitializeData, "get_prepare_init_models", lambda: [])
    monkeypatch.setattr(database, "_get_schema_indexes", lambda: [required_index])
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'drift.db'}")
    monkeypatch.setattr(database, "async_engine", engine)
    config = Config(str(BASE_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BASE_DIR / "app" / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(metadata.create_all)
            await connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(255) PRIMARY KEY)"))
            for head in heads:
                await connection.execute(text("INSERT INTO alembic_version VALUES (:head)"), {"head": head})
        await startup_policy.validate_database_schema()
        async with engine.begin() as connection:
            await connection.execute(text("DROP INDEX ix_schema_probe_value"))
            if missing == "column":
                await connection.execute(text("ALTER TABLE schema_probe DROP COLUMN value"))
        with pytest.raises(RuntimeError, match="缺少列" if missing == "column" else "缺少索引"):
            await startup_policy.validate_database_schema()
        async with engine.connect() as connection:
            assert await connection.run_sync(lambda sync: inspect(sync).get_indexes("schema_probe")) == []
    finally:
        await engine.dispose()


def test_real_sqlite_bootstrap_is_repeatable_without_resetting_users(tmp_path):
    database_name = tmp_path / "bootstrap"
    environment = {**os.environ, "DATABASE_TYPE": "sqlite", "DATABASE_NAME": str(database_name), "SECRET_KEY": "isolated-startup-policy-test-key-32-chars"}
    command = [sys.executable, "-m", "app.scripts.startup_policy", "--env", "dev"]
    first = subprocess.run(command, cwd=BASE_DIR, env=environment, capture_output=True, timeout=90)
    assert first.returncode == 0, first.stderr.decode()
    engine = create_engine(f"sqlite:///{database_name}.db")
    try:
        with engine.begin() as connection:
            users = connection.execute(text("SELECT COUNT(*) FROM sys_user")).scalar_one()
            connection.execute(text("UPDATE sys_user SET password='retained-user-password' WHERE username='super'"))
        second = subprocess.run(command, cwd=BASE_DIR, env=environment, capture_output=True, timeout=90)
        assert second.returncode == 0, second.stderr.decode()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM sys_user")).scalar_one() == users
            assert connection.execute(text("SELECT password FROM sys_user WHERE username='super'")).scalar_one() == "retained-user-password"
    finally:
        engine.dispose()
