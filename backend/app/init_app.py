import asyncio
import sys
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI
from fastapi.concurrency import asynccontextmanager
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi_limiter import FastAPILimiter
from fastapi_limiter.depends import RateLimiter

from app.core import cache_util

from .common.enums import EnvironmentEnum
from .config.path_conf import BASE_DIR
from .config.setting import settings
from .core.exceptions import handle_exception
from .core.http_limit import http_limit_callback, rate_limit_identifier
from .core.logger import logger
from .core.modules import get_module_routers, run_module_hooks
from .core.redis_crud import RedisCURD
from .scripts.migrate import upgrade_database
from .utils.common_util import import_module, import_modules_async
from .utils.console import console_end, console_start


async def run_startup_migration() -> bool:
    """在数据库初始化前应用所有已纳入版本管理的 Alembic 迁移。

    在工作线程中执行 Alembic ``upgrade head``，可能修改配置指向的数据库结构。

    Returns:
        bool: 是否发现并应用或检查了迁移版本。
    """
    logger.info("⏳ 开始应用数据库迁移")
    applied = await asyncio.to_thread(upgrade_database)
    if applied:
        logger.info("✅ 数据库迁移应用完成")
    else:
        logger.info("ℹ️ 当前没有迁移文件，跳过 Alembic")
    return applied


@asynccontextmanager
async def _startup_schema_lock(redis) -> AsyncGenerator[None, None]:
    """在多个 worker 同时启动时串行执行结构迁移和种子初始化。"""
    lock_key = "fastcore:startup:schema"
    lock_value = uuid4().hex
    redis_crud = RedisCURD(redis)
    deadline = asyncio.get_running_loop().time() + 120
    acquired = False
    while not acquired:
        acquired, _ = await redis_crud.lock(lock_key, expire=300, value=lock_value)
        if acquired:
            break
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("等待数据库启动锁超时")
        await asyncio.sleep(1)

    owner_task = asyncio.current_task()
    lease_lost = False

    async def renew() -> None:
        nonlocal lease_lost
        while True:
            await asyncio.sleep(30)
            if not await redis_crud.renew_lock(lock_key, expire=300, value=lock_value):
                lease_lost = True
                if owner_task is not None:
                    owner_task.cancel()
                return

    renewal_task = asyncio.create_task(renew())
    try:
        yield
        if lease_lost:
            raise RuntimeError("数据库启动锁已失效，初始化已中止")
    except asyncio.CancelledError:
        if lease_lost:
            raise RuntimeError("数据库启动锁已失效，初始化已中止") from None
        raise
    finally:
        renewal_task.cancel()
        with suppress(asyncio.CancelledError):
            await renewal_task
        await redis_crud.unlock(lock_key, lock_value)


async def bootstrap_database(redis, environment: EnvironmentEnum | None = None, *, migrate_only: bool = False) -> None:
    """在租约保护的子进程中执行迁移，并按需初始化种子数据。"""
    async with _startup_schema_lock(redis):
        command = [
            sys.executable,
            "-m",
            "app.scripts.startup_policy",
            "--env",
            (environment or settings.ENVIRONMENT).value,
        ]
        if migrate_only:
            command.append("--migrate-only")
        process = await asyncio.create_subprocess_exec(*command, cwd=BASE_DIR)
        try:
            return_code = await process.wait()
        except BaseException:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
            raise
        if return_code != 0:
            raise RuntimeError(f"数据库初始化失败，退出码 {return_code}")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[Any, Any]:
    from app.core.database import async_engine

    events_attempted = modules_attempted = cache_attempted = limiter_attempted = False
    failed = False
    try:
        try:
            if not settings.REDIS_ENABLE:
                raise RuntimeError("Redis 是会话、请求限流和任务协调的必需依赖，REDIS_ENABLE 必须为 true")
            events_attempted = True
            await import_modules_async(modules=settings.EVENT_LIST, desc="全局事件", app=app, status=True)
            logger.info("✅ 全局事件模块加载完成")
            await bootstrap_database(app.state.redis, environment=settings.ENVIRONMENT)
            logger.info("✅ {}数据库结构就绪", settings.DATABASE_TYPE)
            modules_attempted = True
            await run_module_hooks()
            cache_attempted = True
            await cache_util.init(redis=app.state.redis)
            logger.info("✅ fastcore-cache 初始化完成")
            limiter_attempted = True
            await FastAPILimiter.init(
                redis=app.state.redis,
                prefix=settings.REQUEST_LIMITER_REDIS_PREFIX,
                identifier=rate_limit_identifier,
                http_callback=http_limit_callback,
            )
            logger.info("✅ 请求限流器初始化完成")
        except Exception as error:
            logger.error("❌ 应用初始化失败: {}", error)
            raise SystemExit(1) from error

        with suppress(Exception):
            console_start(
                host=settings.SERVER_HOST,
                port=settings.SERVER_PORT,
                reload=settings.ENVIRONMENT,
                database_ready=True,
                redis_ready=True,
                limiter_ready=True,
            )
        yield
    except BaseException:
        failed = True
        raise
    finally:
        # 部分初始化也需要清理；模块任务必须先于它们使用的 Redis/数据库关闭。
        cleanups = []
        if modules_attempted:
            cleanups.append(("业务模块", lambda: run_module_hooks(shutdown=True)))
        if cache_attempted:
            cleanups.append(("fastcore-cache", cache_util.clear))
        if limiter_attempted:
            cleanups.append(("请求限流器", FastAPILimiter.close))
        if events_attempted and getattr(app.state, "redis", None) is not None:
            cleanups.append(("全局事件", lambda: import_modules_async(modules=settings.EVENT_LIST, desc="全局事件", app=app, status=False)))
        cleanups.append(("数据库引擎", async_engine.dispose))
        errors: list[BaseException] = []
        for name, cleanup in cleanups:
            try:
                await cleanup()
            except BaseException as error:
                errors.append(error)
                logger.error("❌ {}关闭失败: {}", name, error)
        with suppress(Exception):
            console_end()
        if errors and not failed:
            raise BaseExceptionGroup("应用资源关闭失败", errors)


def register_middlewares(app: FastAPI) -> None:
    for middleware in settings.MIDDLEWARE_LIST[::-1]:
        if not middleware:
            continue
        middleware = import_module(middleware, desc="中间件")
        app.add_middleware(middleware)


def register_exceptions(app: FastAPI) -> None:
    handle_exception(app)


def register_routers(app: FastAPI) -> None:
    from app.api.v1.module_common import common_router, health_router
    from app.api.v1.module_platform import platform_router
    from app.api.v1.module_system import system_router

    times, seconds = settings.RATE_LIMITER_TIMES, settings.RATE_LIMITER_SECONDS
    if times <= 0 or seconds <= 0:
        raise ValueError("RATE_LIMITER_TIMES 和 RATE_LIMITER_SECONDS 必须为正整数")
    app.include_router(health_router, dependencies=[])
    app.include_router(common_router, dependencies=[Depends(RateLimiter(times=times, seconds=seconds))])
    app.include_router(platform_router, dependencies=[Depends(RateLimiter(times=times, seconds=seconds))])
    app.include_router(system_router, dependencies=[Depends(RateLimiter(times=times, seconds=seconds))])

    for router in get_module_routers():
        app.include_router(router=router, dependencies=[Depends(RateLimiter(times=times, seconds=seconds))])


def register_files(app: FastAPI) -> None:
    if settings.STATIC_ENABLE:
        settings.STATIC_ROOT.mkdir(parents=True, exist_ok=True)
        app.mount(path=settings.STATIC_URL, app=StaticFiles(directory=settings.STATIC_ROOT), name=settings.STATIC_DIR)


def reset_api_docs(app: FastAPI) -> None:
    swagger_ui_redirect_url = str(app.swagger_ui_oauth2_redirect_url)
    root_openapi_url = str(app.root_path) + str(app.openapi_url)

    @app.get(swagger_ui_redirect_url, include_in_schema=False)
    async def swagger_ui_redirect():
        return get_swagger_ui_oauth2_redirect_html()

    @app.get(settings.DOCS_URL, include_in_schema=False)
    async def custom_swagger_ui_html() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=root_openapi_url,
            title=app.title + " - Swagger UI",
            oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
            swagger_js_url=settings.SWAGGER_JS_URL,
            swagger_css_url=settings.SWAGGER_CSS_URL,
            swagger_favicon_url=settings.FAVICON_URL,
        )

    @app.get(settings.REDOC_URL, include_in_schema=False)
    async def custom_redoc_html():
        return get_redoc_html(
            openapi_url=root_openapi_url,
            title=app.title + " - ReDoc",
            redoc_js_url=settings.REDOC_JS_URL,
            redoc_favicon_url=settings.FAVICON_URL,
        )
