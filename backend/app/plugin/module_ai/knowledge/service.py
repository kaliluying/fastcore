from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update

from app.core.base_schema import AuthSchema
from app.core.exceptions import CustomException
from app.core.permission import Permission
from app.plugin.module_ai.config import settings

from .bm25_index import BM25KnowledgeIndex
from .chroma_store import ChromaKnowledgeStore
from .crud import KnowledgeBaseCRUD, KnowledgeChunkCRUD, KnowledgeDocumentCRUD
from .document_lifecycle import KnowledgeDocumentLifecycle, resolve_knowledge_path, safe_knowledge_path
from .embedding import EmbeddingClient
from .extractors import DocumentExtractionError, extract_text
from .model import KnowledgeBaseModel, KnowledgeDocumentModel
from .public import KnowledgeRetriever, accessible_knowledge_base_ids
from .schema import (
    KnowledgeBaseCreateSchema,
    KnowledgeBaseOutSchema,
    KnowledgeBaseQueryParam,
    KnowledgeBaseUpdateSchema,
    KnowledgeChunkOutSchema,
    KnowledgeDocumentOutSchema,
    KnowledgeDocumentQueryParam,
    KnowledgeStatsSchema,
    RetrievalTestSchema,
)


class KnowledgeService:
    def __init__(
        self,
        auth: AuthSchema,
        *,
        store: ChromaKnowledgeStore | None = None,
        embedding_client: EmbeddingClient | None = None,
        bm25_index: BM25KnowledgeIndex | None = None,
    ) -> None:
        self.auth = auth
        self.store = store
        self.embedding_client = embedding_client
        self.bm25_index = bm25_index

    async def page_knowledge_bases(
        self,
        *,
        page_no: int,
        page_size: int,
        search: KnowledgeBaseQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        query = vars(search) if search else {}
        if query.get("name"):
            query["name"] = ("like", query["name"])
        base_crud = KnowledgeBaseCRUD(self.auth)
        result = await base_crud.page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=query,
            out_schema=KnowledgeBaseOutSchema,
        )
        kb_ids = [item["id"] for item in result.items]
        document_counts = await base_crud.count_documents_bulk(kb_ids)
        status_counts_by_kb = await base_crud.count_documents_by_index_status_bulk(kb_ids)
        for item in result.items:
            status_counts = status_counts_by_kb.get(item["id"], {})
            item["document_count"] = document_counts.get(item["id"], 0)
            item["indexed_document_count"] = status_counts.get("success", 0)
            item["indexing_document_count"] = status_counts.get("pending", 0) + status_counts.get("indexing", 0)
            item["failed_document_count"] = status_counts.get("failed", 0)
        return result.model_dump()

    async def get_stats(self) -> KnowledgeStatsSchema:
        """统计当前账号可见的知识库与文档状态分布，供首页概览使用。"""
        base_sql = select(func.count(KnowledgeBaseModel.id)).where(KnowledgeBaseModel.is_deleted == False)  # noqa: E712
        base_sql = await Permission(model=KnowledgeBaseModel, auth=self.auth).filter_query(base_sql)
        knowledge_bases = (await self.auth.db.execute(base_sql)).scalar_one() or 0

        doc_sql = (
            select(KnowledgeDocumentModel.index_status, func.count(KnowledgeDocumentModel.id))
            .where(KnowledgeDocumentModel.is_deleted == False)  # noqa: E712
            .group_by(KnowledgeDocumentModel.index_status)
        )
        doc_sql = await Permission(model=KnowledgeDocumentModel, auth=self.auth).filter_query(doc_sql)
        counts = {status: int(total) for status, total in (await self.auth.db.execute(doc_sql)).all()}
        return KnowledgeStatsSchema(
            knowledge_bases=int(knowledge_bases),
            documents=sum(counts.values()),
            indexed_documents=counts.get("success", 0),
            processing_documents=counts.get("pending", 0) + counts.get("indexing", 0),
            failed_documents=counts.get("failed", 0),
        )

    async def list_enabled_bases(self) -> list[KnowledgeBaseOutSchema]:
        objs = await KnowledgeBaseCRUD(self.auth).get_list(search={"is_enabled": True}, order_by=[{"id": "desc"}])
        return [KnowledgeBaseOutSchema.model_validate(obj) for obj in objs]

    async def create_knowledge_base(self, data: KnowledgeBaseCreateSchema) -> KnowledgeBaseOutSchema:
        obj = await KnowledgeBaseCRUD(self.auth).create(data=data)
        return KnowledgeBaseOutSchema.model_validate(obj)

    async def update_knowledge_base(self, knowledge_base_id: int, data: KnowledgeBaseUpdateSchema) -> KnowledgeBaseOutSchema:
        obj = await KnowledgeBaseCRUD(self.auth).update(id=knowledge_base_id, data=data)
        return KnowledgeBaseOutSchema.model_validate(obj)

    async def delete_knowledge_base(self, ids: list[int]) -> None:
        if not ids:
            raise CustomException(msg="knowledge base ids cannot be empty")
        bases = await KnowledgeBaseCRUD(self.auth).get_list(search={"id": ("in", ids)})
        if {base.id for base in bases} != set(ids):
            raise CustomException(msg="知识库不存在或无权访问", status_code=403)
        await self.auth.db.execute(update(KnowledgeBaseModel).where(KnowledgeBaseModel.id.in_(ids)).values(updated_time=datetime.now()))
        docs = await KnowledgeDocumentCRUD(self.auth).get_list(search={"knowledge_base_id": ("in", ids)})
        doc_ids = [doc.id for doc in docs]
        if doc_ids:
            await KnowledgeDocumentLifecycle(self.auth, store=self.store, embedding_client=self.embedding_client, bm25_index=self.bm25_index).delete_document(doc_ids, knowledge_base_ids=ids)
        else:
            await KnowledgeBaseCRUD(self.auth).delete(ids=ids)
            await self.auth.db.commit()

    async def page_documents(
        self,
        *,
        page_no: int,
        page_size: int,
        search: KnowledgeDocumentQueryParam | None = None,
        order_by: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        query = vars(search) if search else {}
        if query.get("file_name"):
            query["file_name"] = ("like", query["file_name"])
        result = await KnowledgeDocumentCRUD(self.auth).page(
            offset=(page_no - 1) * page_size,
            limit=page_size,
            order_by=order_by or [{"id": "desc"}],
            search=query,
            out_schema=KnowledgeDocumentOutSchema,
        )
        chunk_crud = KnowledgeChunkCRUD(self.auth)
        document_ids = [item["id"] for item in result.items]
        chunk_counts = await chunk_crud.count_by_document_bulk(document_ids)
        for item in result.items:
            item["chunk_count"] = chunk_counts.get(item["id"], 0)
            item["file_path"] = safe_knowledge_path(item.get("file_path"))
        pending_ids = set(
            (await self.auth.db.execute(select(KnowledgeDocumentModel.id).where(KnowledgeDocumentModel.id.in_(document_ids), KnowledgeDocumentModel.pending_file.is_not(None)))).scalars().all()
        )
        for item in result.items:
            item["replacement_pending"] = item["id"] in pending_ids
        return result.model_dump()

    async def document_file(self, document_id: int) -> tuple[Path, str]:
        document = await KnowledgeDocumentCRUD(self.auth).get_or_404(id=document_id)
        await self.auth.db.refresh(document)
        if not document.file_path:
            raise CustomException(msg="原文件不存在", status_code=404)
        return resolve_knowledge_path(document.file_path), Path(document.file_name.replace("\\", "/")).name

    async def page_chunks(self, document_id: int, *, page_no: int, page_size: int) -> dict[str, Any]:
        await KnowledgeDocumentCRUD(self.auth).get_or_404(id=document_id)
        result = await KnowledgeChunkCRUD(self.auth).page(
            offset=(page_no - 1) * page_size, limit=page_size, search={"document_id": document_id}, order_by=[{"chunk_index": "asc"}], out_schema=KnowledgeChunkOutSchema
        )
        return result.model_dump()

    async def document_content(self, document_id: int) -> str:
        path, _name = await self.document_file(document_id)
        try:
            content = await extract_text(path)
        except DocumentExtractionError as exc:
            raise CustomException(msg=str(exc)) from None
        except Exception:
            raise CustomException(msg="无法读取文档内容，请检查文件是否损坏或加密") from None
        if len(content) > 2_000_000:
            raise CustomException(msg="文档内容超过在线编辑上限，请下载后替换文件")
        return content

    async def query_retrieval(self, data: RetrievalTestSchema) -> dict[str, Any]:
        if not data.knowledge_base_ids:
            raise CustomException(msg="please select at least one knowledge base")
        knowledge_base_ids = await accessible_knowledge_base_ids(self.auth, data.knowledge_base_ids)
        retrieval_mode = settings.RETRIEVAL_MODE
        searcher = KnowledgeRetriever(
            mode=retrieval_mode if retrieval_mode in {"vector", "bm25", "hybrid"} else "vector",
            chroma_store=self.store,
            bm25_index=self.bm25_index,
            embedding_client=self.embedding_client,
            alpha=settings.HYBRID_ALPHA,
            top_k=data.top_k,
            candidate_multiplier=settings.RETRIEVAL_CANDIDATE_MULTIPLIER,
            auto_adjust_alpha=getattr(settings, "RETRIEVAL_AUTO_ADJUST_ALPHA", True),
        )
        results = await searcher.search(query=data.query, knowledge_base_ids=knowledge_base_ids)

        if retrieval_mode == "bm25":
            return {
                "query": data.query,
                "retrieval_mode": retrieval_mode,
                "results": [
                    {
                        "content": item.content,
                        "metadata": item.metadata,
                        "score": item.score,
                    }
                    for item in results
                ],
            }

        if retrieval_mode == "hybrid":
            return {
                "query": data.query,
                "retrieval_mode": retrieval_mode,
                "results": [
                    {
                        "content": item.content,
                        "metadata": item.metadata,
                        "distance": item.distance,
                        "score": item.score,
                    }
                    for item in results
                ],
            }

        return {
            "query": data.query,
            "retrieval_mode": retrieval_mode,
            "results": [
                {
                    "content": item.content,
                    "metadata": item.metadata,
                    "distance": item.distance,
                }
                for item in results
            ],
        }
