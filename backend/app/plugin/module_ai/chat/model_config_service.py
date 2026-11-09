from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from app.api.v1.module_system.user.model import UserModel  # noqa: F401
from app.config.setting import settings as core_settings
from app.core.base_schema import AuthSchema
from app.core.database import async_db_session
from app.core.exceptions import CustomException
from app.core.logger import logger
from app.plugin.module_ai.config import async_validate_model_base_url, settings, validate_model_base_url

from .model import AiModelConfigModel
from .schema import AiModelConfigOutSchema, AiModelConfigUpdateSchema, AiModelListRequestSchema


@dataclass(frozen=True, slots=True)
class ChatModelRuntimeConfig:
    protocol: str
    base_url: str
    model: str
    api_key: str


_active_config: ChatModelRuntimeConfig | None = None


def _fernet() -> Fernet:
    # 从现有应用密钥派生稳定的加密密钥。
    key = base64.urlsafe_b64encode(hashlib.sha256(core_settings.SECRET_KEY.encode("utf-8")).digest())
    return Fernet(key)


def _encrypt_api_key(api_key: str) -> str:
    return _fernet().encrypt(api_key.encode("utf-8")).decode("ascii")


def _decrypt_api_key(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError):
        logger.warning("AI model API key could not be decrypted; falling back to environment configuration")
        return None


def _environment_config() -> ChatModelRuntimeConfig:
    return ChatModelRuntimeConfig(
        protocol="openai",
        # 环境文件属于部署配置；用户在数据库中编辑的配置值
        # 仍须通过下方包含 DNS 检查的校验器。
        base_url=validate_model_base_url(settings.OPENAI_BASE_URL, resolve_dns=False) if settings.OPENAI_BASE_URL.strip() else "",
        model=settings.OPENAI_MODEL.strip(),
        api_key=settings.OPENAI_API_KEY.strip(),
    )


def get_active_chat_model_config() -> ChatModelRuntimeConfig:
    return _active_config or _environment_config()


def _apply_record(record: AiModelConfigModel | None) -> ChatModelRuntimeConfig:
    global _active_config
    if record is None:
        _active_config = _environment_config()
        return _active_config

    _active_config = ChatModelRuntimeConfig(
        protocol=record.protocol if record.protocol in {"openai", "openai_responses", "anthropic"} else "openai",
        # 持久化 URL 在保存时已完成校验；DNS 解析被拦截时，
        # 不能导致现有配置在重启后无法加载。
        base_url=validate_model_base_url(record.openai_base_url, resolve_dns=False),
        model=record.openai_model.strip(),
        api_key=_decrypt_api_key(record.encrypted_api_key) or _environment_config().api_key,
    )
    return _active_config


async def _find_record(auth: AuthSchema) -> AiModelConfigModel | None:
    db = getattr(auth, "db", None)
    if db is None or not hasattr(db, "execute"):
        return None
    result = await db.execute(select(AiModelConfigModel).where(AiModelConfigModel.is_deleted.is_(False)).order_by(AiModelConfigModel.id.asc()).limit(1))
    return result.scalars().first()


async def load_runtime_chat_model_config(auth: AuthSchema) -> ChatModelRuntimeConfig:
    """为当前进程或请求加载持久化的覆盖配置（若存在）。"""
    record = await _find_record(auth)
    return _apply_record(record)


async def initialize_runtime_chat_model_config() -> ChatModelRuntimeConfig:
    """在应用启动时加载持久化的覆盖配置。"""
    async with async_db_session() as db:
        async with db.begin():
            auth = AuthSchema(db=db, check_data_scope=False)
            return await load_runtime_chat_model_config(auth)


def _to_output(config: ChatModelRuntimeConfig) -> AiModelConfigOutSchema:
    api_key = config.api_key.strip()
    return AiModelConfigOutSchema(
        chat_protocol=config.protocol,  # type: ignore[arg-type]
        openai_base_url=config.base_url,
        openai_model=config.model,
        openai_embedding_model=settings.OPENAI_EMBEDDING_MODEL,
        embedding_provider=settings.EMBEDDING_PROVIDER,
        local_embedding_model=settings.LOCAL_EMBEDDING_MODEL,
        openai_api_key_configured=bool(api_key and api_key != "your_api_key"),
        chroma_persist_dir=settings.CHROMA_PERSIST_DIR,
        chroma_collection_name=settings.CHROMA_COLLECTION_NAME,
    )


async def get_model_config(auth: AuthSchema) -> AiModelConfigOutSchema:
    return _to_output(await load_runtime_chat_model_config(auth))


async def list_provider_models(auth: AuthSchema, data: AiModelListRequestSchema) -> list[str]:
    """使用编辑后的设置列出服务商模型，不持久化凭证。"""
    current = await load_runtime_chat_model_config(auth)
    same_endpoint = data.openai_base_url == current.base_url
    base_url = await async_validate_model_base_url(data.openai_base_url, resolve_dns=not same_endpoint)
    parsed = urlsplit(base_url)
    if parsed.query or parsed.fragment:
        raise CustomException(msg="API 地址不能包含查询参数或片段")

    api_key = data.openai_api_key
    same_protocol = data.chat_protocol == current.protocol or {data.chat_protocol, current.protocol} <= {"openai", "openai_responses"}
    if not api_key and same_protocol and same_endpoint:
        api_key = current.api_key
    if not api_key or api_key == "your_api_key":
        raise CustomException(msg="请先填写当前接口的 API Key")

    if data.chat_protocol == "anthropic":
        url = f"{base_url.rstrip('/')}/models" if parsed.path.rstrip("/").endswith("/v1") else f"{base_url.rstrip('/')}/v1/models"
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
    else:
        url = f"{base_url.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {api_key}"}

    models: list[str] = []
    cursor: str | None = None
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            for _ in range(10):
                params = {"limit": 1000, "after_id": cursor} if data.chat_protocol == "anthropic" else None
                response = await client.get(url, headers=headers, params=params)
                if response.status_code != 200:
                    raise CustomException(msg=f"获取模型失败：服务端返回 HTTP {response.status_code}")
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise CustomException(msg="获取模型失败：服务端返回的数据格式不正确")
                models.extend(
                    item["id"] for item in payload["data"]
                    if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
                )
                if data.chat_protocol != "anthropic" or not payload.get("has_more"):
                    return sorted(set(models))
                next_cursor = payload.get("last_id")
                if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                    raise CustomException(msg="获取模型失败：服务端分页数据不完整")
                cursor = next_cursor
    except (httpx.RequestError, ValueError) as exc:
        raise CustomException(msg="获取模型失败：无法连接接口或解析响应") from exc
    raise CustomException(msg="获取模型失败：模型列表超过分页上限")


async def update_model_config(
    auth: AuthSchema,
    data: AiModelConfigUpdateSchema,
) -> AiModelConfigOutSchema:
    record = await _find_record(auth)
    current = get_active_chat_model_config()
    same_endpoint = data.openai_base_url == current.base_url
    base_url = await async_validate_model_base_url(data.openai_base_url, resolve_dns=not same_endpoint)
    parsed = urlsplit(base_url)
    if parsed.query or parsed.fragment:
        raise CustomException(msg="API 地址不能包含查询参数或片段")
    same_credentials_scope = same_endpoint and (
        data.chat_protocol == current.protocol
        or {data.chat_protocol, current.protocol} <= {"openai", "openai_responses"}
    )
    if not data.openai_api_key and not same_credentials_scope:
        raise CustomException(msg="更换接口地址或协议时，请填写该接口的 API Key")
    if record is None:
        record = AiModelConfigModel(
            protocol=data.chat_protocol,
            openai_base_url=base_url,
            openai_model=data.openai_model,
            encrypted_api_key=_encrypt_api_key(data.openai_api_key or current.api_key) if (data.openai_api_key or current.api_key) else None,
            created_id=getattr(getattr(auth, "user", None), "id", None),
        )
        auth.db.add(record)
    else:
        record.protocol = data.chat_protocol
        record.openai_base_url = base_url
        record.openai_model = data.openai_model
        if data.openai_api_key:
            record.encrypted_api_key = _encrypt_api_key(data.openai_api_key)
        record.updated_id = getattr(getattr(auth, "user", None), "id", None)

    await auth.db.flush()
    _apply_record(record)
    return _to_output(get_active_chat_model_config())
