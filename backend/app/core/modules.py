"""业务模块的显式组装边界，不扫描目录或读取运行时开关。"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from importlib import import_module
from pathlib import Path

from fastapi import APIRouter

from app.common.enums import EnvironmentEnum
from app.config import modules as module_config
from app.config.setting import settings
from app.core.base_model import MappedBase


@dataclass(frozen=True)
class ModuleSpec:
    name: str
    routers: tuple[str, ...] = ()
    model_modules: tuple[str, ...] = ()
    permissions: frozenset[str] = frozenset()
    seed_dir: Path | None = None
    startup_hooks: tuple[str, ...] = ()
    shutdown_hooks: tuple[str, ...] = ()
    schema_indexes: str | None = None
    backup_sources: str | None = None
    development_only: bool = False
    menu_roots: tuple[str, ...] = ()
    permission_prefixes: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return not self.development_only or settings.ENVIRONMENT == EnvironmentEnum.DEV


def resolve_entrypoint(entrypoint: str):
    module, separator, name = entrypoint.partition(":")
    if not separator or not module or not name:
        raise ValueError(f"模块入口必须使用 module:attribute 形式: {entrypoint!r}")
    return getattr(import_module(module), name)


@lru_cache(maxsize=1)
def get_modules() -> tuple[ModuleSpec, ...]:
    modules = tuple(resolve_entrypoint(entry)() for entry in module_config.INSTALLED_MODULES)
    if any(not isinstance(module, ModuleSpec) or not module.name for module in modules):
        raise TypeError("模块入口必须返回有名称的 ModuleSpec")
    if len({module.name for module in modules}) != len(modules):
        raise ValueError("模块名称不能重复")
    return modules


def get_module_routers() -> tuple[APIRouter, ...]:
    routers = []
    for module in get_modules():
        if not module.active:
            continue
        for entry in module.routers:
            router = resolve_entrypoint(entry)
            if callable(router) and not isinstance(router, APIRouter):
                router = router()
            if not isinstance(router, APIRouter):
                raise TypeError(f"模块路由入口不是 APIRouter: {entry}")
            routers.append(router)
    return tuple(routers)


def get_module_models() -> list[type[MappedBase]]:
    models = []
    for module in get_modules():
        for path in module.model_modules:
            for candidate in vars(import_module(path)).values():
                if isinstance(candidate, type) and issubclass(candidate, MappedBase) and candidate is not MappedBase and getattr(candidate, "__tablename__", None) and candidate not in models:
                    models.append(candidate)
    return models


async def run_module_hooks(*, shutdown: bool = False) -> None:
    modules = get_modules()
    errors: list[BaseException] = []
    for module in reversed(modules) if shutdown else modules:
        if not module.active:
            continue
        for entry in module.shutdown_hooks if shutdown else module.startup_hooks:
            try:
                hook: Callable = resolve_entrypoint(entry)
                result = hook()
                if inspect.isawaitable(result):
                    await result
            except BaseException as error:
                if not shutdown:
                    raise
                errors.append(error)
    if errors:
        raise BaseExceptionGroup("业务模块关闭失败", errors)


def menu_is_active(*, permission: str | None, route_name: str | None) -> bool:
    return not any(not module.active and (route_name in module.menu_roots or (permission or "").startswith(module.permission_prefixes)) for module in get_modules())


def include_owned_schema_object(obj, name, type_, reflected, compare_to) -> bool:
    # 被裁剪模块的历史表不归当前模型管理，生成迁移时保留它们。
    return not (type_ == "table" and reflected and compare_to is None)
