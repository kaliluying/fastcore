"""注册核心 AI 模块。

后端当前只有 ``module_ai`` 一个 AI 模块，其 ``plugin.toml`` 统一声明路由、
模型、启动 hook、权限和依赖检查。当前不提供插件发现或热重载机制。
"""

import importlib
import importlib.util
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter

AI_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "plugin" / "module_ai" / "plugin.toml"


@dataclass(frozen=True)
class AiPluginManifest:
    """描述核心 AI 模块，避免提前导入模块实现。"""

    package: str
    name: str
    title: str
    route_prefix: str
    required_modules: tuple[str, ...]
    routers: tuple[str, ...]
    model_modules: tuple[str, ...]
    startup_hooks: tuple[str, ...]
    permissions: tuple[str, ...]


def _as_tuple(value: Any, field_name: str) -> tuple[str, ...]:
    """校验 AI 清单中的字符串列表字段。

    Args:
        value: 已解析的 TOML 字段值。
        field_name: 用于诊断信息的字段名。

    Returns:
        由非空字符串组成的元组。

    Raises:
        ValueError: 配置值不是字符串列表。
    """
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{AI_MANIFEST_PATH} 的 {field_name} 必须是非空字符串数组")
    return tuple(value)


@lru_cache(maxsize=1)
def get_ai_plugin_manifest() -> AiPluginManifest:
    """加载并校验核心 AI 模块清单。

    Returns:
        已解析的 AI 模块元数据。

    Raises:
        ValueError: 必需的 AI 模块元数据无效。
    """
    with AI_MANIFEST_PATH.open("rb") as file:
        raw = tomllib.load(file)

    package = raw.get("package")
    name = raw.get("name")
    title = raw.get("title", name)
    route_prefix = raw.get("route_prefix")
    if package != "module_ai" or name != "ai":
        raise ValueError(f"{AI_MANIFEST_PATH} 必须声明 module_ai / ai")
    if not isinstance(title, str) or not title:
        raise ValueError(f"{AI_MANIFEST_PATH} 必须声明 title")
    if not isinstance(route_prefix, str) or not route_prefix.startswith("/"):
        raise ValueError(f"{AI_MANIFEST_PATH} 的 route_prefix 必须以 / 开头")

    return AiPluginManifest(
        package=package,
        name=name,
        title=title,
        route_prefix=route_prefix.rstrip("/"),
        required_modules=_as_tuple(raw.get("required_modules"), "required_modules"),
        routers=_as_tuple(raw.get("routers"), "routers"),
        model_modules=_as_tuple(raw.get("model_modules"), "model_modules"),
        startup_hooks=_as_tuple(raw.get("startup_hooks"), "startup_hooks"),
        permissions=_as_tuple(raw.get("permissions"), "permissions"),
    )


def _missing_dependencies(manifest: AiPluginManifest) -> tuple[str, ...]:
    """返回 AI 模块依赖中不可用的 Python 模块名。"""
    return tuple(module for module in manifest.required_modules if importlib.util.find_spec(module) is None)


def ensure_ai_dependencies() -> None:
    """在核心 AI 依赖不可用时立即终止启动。

    Raises:
        RuntimeError: 必需的 AI 依赖包或模块目录不存在。
    """
    manifest = get_ai_plugin_manifest()
    if importlib.util.find_spec(f"app.plugin.{manifest.package}") is None:
        raise RuntimeError(f"{manifest.title} 模块目录不存在: app.plugin.{manifest.package}")

    missing = _missing_dependencies(manifest)
    if missing:
        raise RuntimeError(f"{manifest.title} 核心依赖缺失：{', '.join(missing)}。请执行 `uv sync` 后重启服务。")


def _resolve_entrypoint(manifest: AiPluginManifest, entrypoint: str) -> Any:
    """导入 AI 清单声明的入口。

    Args:
        manifest: 已校验的 AI 模块元数据。
        entrypoint: ``plugin.toml`` 中的 ``module:attribute`` 入口值。

    Returns:
        AI 模块导出的属性。

    Raises:
        ValueError: 入口格式无效。
    """
    module_path, separator, attribute = entrypoint.partition(":")
    if not separator or not module_path or not attribute:
        raise ValueError(f"AI 模块入口必须使用 module:attribute 形式: {entrypoint!r}")
    module = importlib.import_module(f"app.plugin.{manifest.package}.{module_path}")
    return getattr(module, attribute)


def get_ai_routers() -> tuple[APIRouter, ...]:
    """加载核心 AI 清单声明的 HTTP 路由。"""
    ensure_ai_dependencies()
    manifest = get_ai_plugin_manifest()
    container_router = APIRouter(prefix=manifest.route_prefix)
    for entrypoint in manifest.routers:
        router = _resolve_entrypoint(manifest, entrypoint)
        if not isinstance(router, APIRouter):
            raise TypeError(f"AI 模块路由入口不是 APIRouter: {entrypoint}")
        container_router.include_router(router)
    return (container_router,)



__all__ = ["ensure_ai_dependencies", "get_ai_plugin_manifest", "get_ai_routers"]
