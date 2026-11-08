"""AI 的组装、索引与备份入口归属 AI 模块本身。"""

from functools import lru_cache
from pathlib import Path

from sqlalchemy import Index

from app.config.path_conf import BASE_DIR
from app.core.modules import ModuleSpec
from app.core.plugins import ensure_ai_dependencies, get_ai_plugin_manifest, get_ai_routers


def get_module() -> ModuleSpec:
    ensure_ai_dependencies()
    manifest = get_ai_plugin_manifest()
    package = f"app.plugin.{manifest.package}"
    return ModuleSpec(
        name=manifest.name,
        routers=(f"{package}.module:get_http_router",),
        model_modules=tuple(f"{package}.{path}" for path in manifest.model_modules),
        permissions=frozenset(manifest.permissions),
        seed_dir=Path(__file__).parent / "data",
        startup_hooks=tuple(f"{package}.{entry}" for entry in manifest.startup_hooks),
        shutdown_hooks=(
            f"{package}.agent.runtime:stop_agent_worker",
            f"{package}.chat.memory_worker:stop_memory_extraction",
            f"{package}.knowledge.recovery:stop_knowledge_recovery",
        ),
        schema_indexes=f"{package}.module:get_schema_indexes",
        backup_sources=f"{package}.module:get_backup_sources",
        menu_roots=("AI",),
        permission_prefixes=("module_ai:",),
    )


def get_http_router():
    return get_ai_routers()[0]


def get_backup_sources() -> dict[str, Path]:
    from app.plugin.module_ai.config import settings

    return {
        "knowledge": BASE_DIR / "storage" / "knowledge",
        "chroma": Path(settings.CHROMA_PERSIST_DIR),
        "bm25": Path(settings.BM25_INDEX_DIR or BASE_DIR / "data" / "bm25_index"),
    }


@lru_cache(maxsize=1)
def get_schema_indexes() -> tuple[Index, ...]:
    from app.plugin.module_ai.chat.model import ChatSessionModel
    from app.plugin.module_ai.knowledge.model import KnowledgeChunkModel, KnowledgeDocumentModel
    from app.plugin.module_ai.memory.model import AiMemoryModel

    indexes = [
        Index(
            "ix_opt_ai_document_base_status_deleted",
            KnowledgeDocumentModel.knowledge_base_id,
            KnowledgeDocumentModel.index_status,
            KnowledgeDocumentModel.is_deleted,
        ),
        Index(
            "ix_opt_ai_chunk_document_deleted_index",
            KnowledgeChunkModel.document_id,
            KnowledgeChunkModel.is_deleted,
            KnowledgeChunkModel.chunk_index,
        ),
        Index(
            "ix_opt_ai_session_user_deleted_updated",
            ChatSessionModel.user_id,
            ChatSessionModel.is_deleted,
            ChatSessionModel.updated_time,
        ),
        Index(
            "ix_opt_ai_memory_user_active_deleted_priority",
            AiMemoryModel.user_id,
            AiMemoryModel.is_active,
            AiMemoryModel.is_deleted,
            AiMemoryModel.priority,
        ),
        Index(
            "ix_opt_ai_memory_user_key_deleted",
            AiMemoryModel.user_id,
            AiMemoryModel.key,
            AiMemoryModel.is_deleted,
        ),
    ]

    return tuple(indexes)
