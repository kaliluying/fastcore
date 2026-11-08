from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Query
from pydantic import BaseModel, Field, field_validator

from app.core.base_params import BaseQueryParam, UserByQueryParam
from app.plugin.module_ai.config import validate_model_base_url

from .runtime_logs import RuntimeLogQuerySchema


class ChatQuerySchema(BaseModel):
    """SSE 流式聊天请求。"""

    message: str = Field(..., min_length=1, max_length=8_000, description="Message content")
    session_id: str | None = Field(None, description="Session ID")
    files: list[dict[str, Any]] | None = Field(None, description="Ad-hoc file context")
    knowledge_base_ids: list[int] = Field(default_factory=list, max_length=20, description="Knowledge base IDs")
    request_id: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    log_query: RuntimeLogQuerySchema | None = None
    log_context_id: str | None = Field(None, max_length=32, pattern=r"^[a-f0-9]{32}$")

    @field_validator("files")
    @classmethod
    def validate_files(cls, value: list[dict[str, Any]] | None):
        if value and (len(value) > 5 or any(len(str(item.get("content", ""))) > 16_000 for item in value)):
            raise ValueError("最多发送 5 个附件，每个附件正文不超过 16000 字符")
        return value


class ChatSessionCreateSchema(BaseModel):
    """创建聊天会话的请求。"""

    title: str = Field(..., min_length=1, max_length=200, description="Session title")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 1 or len(value) > 200:
            raise ValueError("会话标题长度必须在 1-200 个字符之间")
        return value


class ChatSessionUpdateSchema(BaseModel):
    """更新聊天会话的请求。"""

    title: str = Field(..., min_length=1, max_length=200, description="Session title")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 1 or len(value) > 200:
            raise ValueError("会话标题长度必须在 1-200 个字符之间")
        return value


@dataclass
class ChatSessionQueryParam(BaseQueryParam, UserByQueryParam):
    """聊天会话列表查询。"""

    title: str | None = Query(None, description="Session title")


class AiChatRequestSchema(BaseModel):
    """非流式 AI 聊天请求。"""

    message: str = Field(..., min_length=1, max_length=8_000, description="User message")
    session_id: str | None = Field(None, description="Session ID; creates a new session when omitted")
    knowledge_base_ids: list[int] = Field(default_factory=list, max_length=20, description="Knowledge base IDs")
    log_context_id: str | None = Field(None, max_length=32, pattern=r"^[a-f0-9]{32}$")

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 1:
            raise ValueError("用户消息内容不能为空")
        return value


class AiChatResponseSchema(BaseModel):
    """非流式 AI 聊天响应。"""

    response: str = Field(..., description="AI response content")
    session_id: str = Field(..., description="Session ID")
    function_calls: list[dict[str, Any]] | None = Field(None, description="Function call metadata")
    action: dict[str, Any] | None = Field(None, description="Suggested action")
    log_context_id: str | None = None


ChatProtocol = Literal["openai", "openai_responses", "anthropic"]


class AiModelConfigOutSchema(BaseModel):
    """可安全展示的 AI 运行配置状态。"""

    chat_protocol: ChatProtocol
    openai_base_url: str
    openai_model: str
    openai_embedding_model: str
    embedding_provider: str
    local_embedding_model: str
    openai_api_key_configured: bool
    chroma_persist_dir: str
    chroma_collection_name: str


class AiModelConfigUpdateSchema(BaseModel):
    """可编辑的聊天模型配置；有意排除向量设置。"""

    chat_protocol: ChatProtocol = "openai"
    openai_base_url: str = Field(..., min_length=1, max_length=500)
    openai_model: str = Field(..., min_length=1, max_length=200)
    openai_api_key: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("openai_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        # 服务层先与已配置的端点比较，再执行 DNS 检查。
        return validate_model_base_url(value, resolve_dns=False)

    @field_validator("openai_model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("对话模型名称不能为空")
        return normalized

    @field_validator("openai_api_key")
    @classmethod
    def normalize_api_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AiModelListRequestSchema(BaseModel):
    """仅供单次模型列表请求使用、不予保存的连接设置。"""

    chat_protocol: ChatProtocol
    openai_base_url: str = Field(..., min_length=1, max_length=500)
    openai_api_key: str | None = Field(default=None, max_length=512)

    @field_validator("openai_base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        # 服务层先将提交的 URL 与已配置的服务商端点比较，
        # 再执行包含 DNS 检查的校验。
        return validate_model_base_url(value, resolve_dns=False)

    @field_validator("openai_api_key")
    @classmethod
    def normalize_api_key(cls, value: str | None) -> str | None:
        return (value.strip() or None) if value is not None else None


class AiUsageQuotaUpdateSchema(BaseModel):
    daily_limit: int | None = Field(default=None, ge=0, le=1_000_000, strict=True, description="每日供应商调用次数；null 表示不限")
