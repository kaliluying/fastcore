import os
from typing import Annotated

import typer
import uvicorn
from alembic import command
from alembic.config import Config
from fastapi import FastAPI

from app.common.enums import EnvironmentEnum

fastcore_cli = typer.Typer()


@fastcore_cli.command(name="generate-module", help="生成独立业务模块；默认仅预览差异，--apply 写入，不连接数据库")
def generate_module(
    module: Annotated[str, typer.Option("--module", help="新功能组标识，如 business")],
    resource: Annotated[str, typer.Option("--resource", help="资源标识，如 supplier")],
    title: Annotated[str, typer.Option("--title", help="资源显示名，如 供应商；页面自动加“管理”")],
    module_title: Annotated[str | None, typer.Option("--module-title", help="目录显示名，默认使用资源显示名")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="写入新文件并接入模块清单")] = False,
) -> None:
    from pathlib import Path

    from app.scripts.module_generator import apply_module_plan, build_module_plan, preview_module

    root = Path(__file__).resolve().parent.parent
    try:
        plan = build_module_plan(root, module=module, resource=resource, title=title, module_title=module_title)
        if apply:
            apply_module_plan(root, plan)
        else:
            typer.echo(preview_module(plan))
    except (ValueError, OSError, SyntaxError) as error:
        typer.echo(f"生成失败: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(f"{'已生成' if apply else '仅预览，未写入'}: {len(plan.files)} 个新文件，更新 backend/app/config/modules.py")
    if apply:
        typer.echo(f"回归: uv run --no-sync pytest tests/test_{module}_{resource}.py -q")
        typer.echo("请先审查迁移和业务约束，再核对目标数据库并启动或 bootstrap；普通角色需在角色管理中明确授权。")
    else:
        typer.echo("确认差异后添加 --apply 执行；已有文件不会覆盖。")


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例并完成日志、中间件、路由与静态资源注册。

    Returns:
        FastAPI: 已配置生命周期的应用对象。
    """
    from app.config.setting import settings
    from app.init_app import lifespan, register_exceptions, register_files, register_middlewares, register_routers, reset_api_docs
    # 创建FastAPI应用
    app = FastAPI(**settings.FASTAPI_CONFIG, lifespan=lifespan)
    # 注册异常处理器
    register_exceptions(app)
    # 注册中间件
    register_middlewares(app)
    # 注册路由
    register_routers(app)
    # 注册静态文件
    register_files(app)
    # 重设API文档
    reset_api_docs(app)
    return app


# typer.Option是非必填；typer.Argument是必填
@fastcore_cli.command(
    name="run",
    help="启动 FastCore 服务, 运行 uv run main.py run --env=dev 不加参数默认 dev 环境",
)
def run(
    env: Annotated[
        EnvironmentEnum, typer.Option("--env", help="运行环境 (dev, prod)")
    ] = EnvironmentEnum.DEV,
) -> None:
    """按指定环境加载配置并启动 Uvicorn（开发环境开启 reload）。

    Args:
        env (EnvironmentEnum): 运行环境，对应 `--env`。
    """
    # 设置环境变量（必须在 import settings/logger 之前，确保加载正确环境；
    # app.core.logger 模块顶层会调用 setup_logger() 间接读取 settings，
    # 因此这里连 logger 也要延迟到环境变量设置之后才 import）
    os.environ["ENVIRONMENT"] = env.value
    from app.config.setting import settings
    from app.core.logger import logger
    from app.utils.banner import worship

    typer.secho(
        message="FastCore 服务启动",
        fg=typer.colors.GREEN,
    )
    logger.info(worship(env.value))

    # 启动uvicorn服务
    uvicorn.run(
        app="main:create_app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=env.value == EnvironmentEnum.DEV.value,
        reload_dirs=["app"] if env.value == EnvironmentEnum.DEV.value else None,
        reload_includes=["main.py"] if env.value == EnvironmentEnum.DEV.value else None,
        factory=True,
        log_config=None,
    )


@fastcore_cli.command(
    name="bootstrap",
    help="首次部署显式建表并导入基础数据，运行 python main.py bootstrap --env=prod；不会清空现有数据",
)
def bootstrap(
    env: Annotated[EnvironmentEnum, typer.Option("--env", help="运行环境 (dev, prod)")] = EnvironmentEnum.DEV,
) -> None:
    """使用与开发启动相同的锁初始化所选数据库。"""
    os.environ["ENVIRONMENT"] = env.value
    from app.config.setting import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    if not settings.REDIS_ENABLE:
        raise typer.BadParameter("Redis 是必需依赖，REDIS_ENABLE 必须为 true")

    import asyncio

    from redis.asyncio import Redis

    from app.init_app import bootstrap_database

    async def initialize() -> None:
        redis = Redis.from_url(settings.REDIS_URI, decode_responses=True, socket_timeout=settings.POOL_TIMEOUT)
        try:
            await redis.ping()
            await bootstrap_database(redis, environment=env)
        finally:
            await redis.aclose()

    asyncio.run(initialize())
    typer.echo("数据库结构与基础数据已初始化；正常启动也会自动应用迁移并补齐基础数据。")


@fastcore_cli.command(
    name="reset",
    help="删除所有表并重建 + 写入种子数据（危险操作）, 运行 python main.py reset --env=dev",
)
def reset(
    env: Annotated[
        EnvironmentEnum, typer.Option("--env", help="运行环境 (dev, prod)")
    ] = EnvironmentEnum.DEV,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="跳过确认，直接执行")
    ] = False,
) -> None:
    """删除全部 ORM 表并重建结构，然后写入基础种子数据。

    单进程内导入 initialize（注册全部模型到 metadata），依次执行
    drop_all → create_all → 种子导入，避免分步执行时元数据不完整。

    Args:
        env (EnvironmentEnum): 运行环境，对应 `--env`。
        yes (bool): 跳过交互式确认，对应 `--yes/-y`。
    """
    os.environ["ENVIRONMENT"] = env.value
    from app.config.setting import get_settings

    get_settings.cache_clear()

    if not yes:
        confirm = typer.confirm(
            f"⚠️  将删除 {env.value} 环境数据库的所有表并重建，数据不可恢复，确认继续？"
        )
        if not confirm:
            typer.echo("已取消")
            raise typer.Exit(code=1)

    import asyncio

    from sqlalchemy import MetaData, inspect
    from sqlalchemy.exc import CompileError
    from sqlalchemy.schema import DropConstraint, DropTable

    # 导入 initialize 会注册全部模型到 metadata（create_all 依赖完整元数据）
    from app.core.database import async_engine, create_tables
    from app.scripts.initialize import InitializeData

    async def _reset() -> None:
        """通过当前数据库方言反射并删除全部表，再重建 ORM 表结构。"""

        def drop_reflected_tables(connection) -> None:
            """通过反射删除数据表和外键，不使用数据库专有 SQL。

            先删除可反射的外键约束，再按逆依赖顺序删表，避免依赖
            MySQL ``information_schema`` 或 ``FOREIGN_KEY_CHECKS``。

            Args:
                connection: 异步连接通过 ``run_sync`` 提供的 SQLAlchemy 同步连接。
            """
            inspector = inspect(connection)
            table_names = inspector.get_table_names()
            if not table_names:
                return

            metadata = MetaData()
            metadata.reflect(bind=connection, only=table_names)
            for table in metadata.tables.values():
                for constraint in table.foreign_key_constraints:
                    try:
                        connection.execute(DropConstraint(constraint))
                    except (CompileError, NotImplementedError):
                        # SQLite 不支持独立删除外键约束，后续删表即可清理。
                        continue

            for table in reversed(list(metadata.tables.values())):
                connection.execute(DropTable(table, if_exists=True))

        async with async_engine.begin() as conn:
            await conn.run_sync(drop_reflected_tables)
        from app.init_app import run_startup_migration

        await run_startup_migration()
        # reset 清表后必须显式恢复完整模型结构，不能等待启动迁移。
        await create_tables()
        await InitializeData().init_db()

    asyncio.run(_reset())
    typer.secho(
        message="数据库已重置并写入种子数据",
        fg=typer.colors.GREEN,
    )


@fastcore_cli.command(
    name="revision",
    help="生成新的 Alembic 迁移脚本, 运行 python main.py revision --env=dev",
)
def revision(
    env: Annotated[
        EnvironmentEnum, typer.Option("--env", help="运行环境 (dev, prod)")
    ] = EnvironmentEnum.DEV,
) -> None:
    """使用 Alembic 自动生成迁移脚本（autogenerate）。

    Args:
        env (EnvironmentEnum): 运行环境，用于加载对应数据库模型元数据。
    """
    os.environ["ENVIRONMENT"] = env.value
    from app.config.setting import get_settings

    get_settings.cache_clear()
    alembic_cfg = Config("alembic.ini")
    command.revision(alembic_cfg, autogenerate=True, message="迁移脚本")
    typer.echo("迁移脚本已生成")


@fastcore_cli.command(
    name="migrate",
    help="部署阶段显式应用迁移，运行 python main.py migrate --env=prod",
)
def migrate(
    env: Annotated[
        EnvironmentEnum, typer.Option("--env", help="运行环境 (dev, prod)")
    ] = EnvironmentEnum.DEV,
) -> None:
    """将数据库升级到 Alembic 最新版本（head）。

    Args:
        env (EnvironmentEnum): 运行环境。
    """
    os.environ["ENVIRONMENT"] = env.value
    from app.config.setting import get_settings

    get_settings.cache_clear()
    import asyncio

    from app.scripts.migrate import migrate_database

    asyncio.run(migrate_database(env))
    typer.echo("所有迁移已应用。")


if __name__ == "__main__":
    fastcore_cli()
