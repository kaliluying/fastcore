from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Form, Path, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.common.response import ResponseSchema, SuccessResponse
from app.core.base_params import PaginationQueryParam
from app.core.base_schema import AuthSchema
from app.core.dependencies import AuthPermission
from app.core.router_class import OperationLogRoute

from .document_lifecycle import KnowledgeDocumentLifecycle
from .schema import (
    DocumentContentUpdateSchema,
    KnowledgeBaseCreateSchema,
    KnowledgeBaseOutSchema,
    KnowledgeBaseQueryParam,
    KnowledgeBaseUpdateSchema,
    KnowledgeDocumentOutSchema,
    KnowledgeDocumentQueryParam,
    KnowledgeStatsSchema,
    RetrievalTestSchema,
)
from .service import KnowledgeService

KnowledgeRouter = APIRouter(route_class=OperationLogRoute, prefix="/knowledge", tags=["AI", "Knowledge"])


@KnowledgeRouter.get(
    "/list",
    summary="List knowledge bases",
    response_model=ResponseSchema[dict[str, Any]],
)
async def list_knowledge_base_controller(
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[KnowledgeBaseQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:knowledge:query"]))],
) -> JSONResponse:
    result = await KnowledgeService(auth).page_knowledge_bases(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="query knowledge bases success")


@KnowledgeRouter.get(
    "/stats",
    summary="Knowledge overview stats",
    response_model=ResponseSchema[KnowledgeStatsSchema],
)
async def knowledge_stats_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:knowledge:query"]))],
) -> JSONResponse:
    result = await KnowledgeService(auth).get_stats()
    return SuccessResponse(data=result, msg="query knowledge stats success")


@KnowledgeRouter.get(
    "/optionselect",
    summary="List enabled knowledge bases",
    response_model=ResponseSchema[list[KnowledgeBaseOutSchema]],
)
async def list_enabled_knowledge_base_controller(
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:knowledge:query"]))],
) -> JSONResponse:
    result = await KnowledgeService(auth).list_enabled_bases()
    return SuccessResponse(data=result, msg="query enabled knowledge bases success")


@KnowledgeRouter.post(
    "/create",
    summary="Create knowledge base",
    response_model=ResponseSchema[KnowledgeBaseOutSchema],
)
async def create_knowledge_base_controller(
    data: KnowledgeBaseCreateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:knowledge:create"]))],
) -> JSONResponse:
    result = await KnowledgeService(auth).create_knowledge_base(data=data)
    return SuccessResponse(data=result, msg="create knowledge base success")


@KnowledgeRouter.put(
    "/update/{id}",
    summary="Update knowledge base",
    response_model=ResponseSchema[KnowledgeBaseOutSchema],
)
async def update_knowledge_base_controller(
    id: Annotated[int, Path(ge=1)],
    data: KnowledgeBaseUpdateSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:knowledge:update"]))],
) -> JSONResponse:
    result = await KnowledgeService(auth).update_knowledge_base(knowledge_base_id=id, data=data)
    return SuccessResponse(data=result, msg="update knowledge base success")


@KnowledgeRouter.delete(
    "/delete",
    summary="Delete knowledge bases",
    response_model=ResponseSchema[None],
)
async def delete_knowledge_base_controller(
    ids: Annotated[list[int], Body()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:knowledge:delete"]))],
) -> JSONResponse:
    await KnowledgeService(auth).delete_knowledge_base(ids=ids)
    return SuccessResponse(msg="delete knowledge base success")


@KnowledgeRouter.get(
    "/document/list",
    summary="List knowledge documents",
    response_model=ResponseSchema[dict[str, Any]],
)
async def list_document_controller(
    page: Annotated[PaginationQueryParam, Depends()],
    search: Annotated[KnowledgeDocumentQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:document:query"]))],
) -> JSONResponse:
    result = await KnowledgeService(auth).page_documents(
        page_no=page.page_no,
        page_size=page.page_size,
        search=search,
        order_by=page.order_by,
    )
    return SuccessResponse(data=result, msg="query knowledge documents success")


@KnowledgeRouter.get("/document/{id}/file", summary="Download original knowledge document")
async def download_document_controller(
    id: Annotated[int, Path(ge=1)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:document:query"]))],
) -> FileResponse:
    path, filename = await KnowledgeService(auth).document_file(id)
    return FileResponse(path, filename=filename, media_type="application/octet-stream", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@KnowledgeRouter.get("/document/{id}/chunks", summary="Read full document chunks", response_model=ResponseSchema[dict[str, Any]])
async def list_document_chunks_controller(
    id: Annotated[int, Path(ge=1)],
    page: Annotated[PaginationQueryParam, Depends()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:document:query"]))],
) -> JSONResponse:
    return SuccessResponse(data=await KnowledgeService(auth).page_chunks(id, page_no=page.page_no, page_size=page.page_size))


@KnowledgeRouter.get("/document/{id}/content", summary="Read document text for correction", response_model=ResponseSchema[str])
async def get_document_content_controller(
    id: Annotated[int, Path(ge=1)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:document:query"]))],
) -> JSONResponse:
    return SuccessResponse(data=await KnowledgeService(auth).document_content(id))


@KnowledgeRouter.put("/document/{id}/content", summary="Correct document text and rebuild indexes", response_model=ResponseSchema[KnowledgeDocumentOutSchema])
async def update_document_content_controller(
    id: Annotated[int, Path(ge=1)],
    data: DocumentContentUpdateSchema,
    background_tasks: BackgroundTasks,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:document:create"]))],
) -> JSONResponse:
    result = await KnowledgeDocumentLifecycle(auth).update_document_content(id, data.content, background_tasks)
    return SuccessResponse(data=result, msg="纠错已接收，索引完成后生效；原文件将转换为 TXT")


@KnowledgeRouter.post("/document/{id}/replace", summary="Replace document after new indexes are ready", response_model=ResponseSchema[KnowledgeDocumentOutSchema])
async def replace_document_controller(
    id: Annotated[int, Path(ge=1)],
    file: UploadFile,
    background_tasks: BackgroundTasks,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:document:create"]))],
) -> JSONResponse:
    result = await KnowledgeDocumentLifecycle(auth).replace_document(id, file, background_tasks)
    return SuccessResponse(data=result, msg="替换已接收，处理完成后生效；失败时保留原文件和原分块")


@KnowledgeRouter.post(
    "/document/upload",
    summary="Upload and index knowledge document",
    response_model=ResponseSchema[KnowledgeDocumentOutSchema],
)
async def upload_document_controller(
    knowledge_base_id: Annotated[int, Form(ge=1)],
    file: UploadFile,
    background_tasks: BackgroundTasks,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:document:create"]))],
) -> JSONResponse:
    result = await KnowledgeDocumentLifecycle(auth).upload_document(
        knowledge_base_id=knowledge_base_id,
        file=file,
        background_tasks=background_tasks,
    )
    return SuccessResponse(data=result, msg="文档已接收，等待后台处理；处理完成后可检索")


@KnowledgeRouter.post(
    "/document/{id}/reindex",
    summary="Reindex knowledge document",
    response_model=ResponseSchema[KnowledgeDocumentOutSchema],
)
async def reindex_document_controller(
    id: Annotated[int, Path(ge=1)],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:document:create"]))],
) -> JSONResponse:
    result = await KnowledgeDocumentLifecycle(auth).index_document(document_id=id)
    return SuccessResponse(data=result, msg="索引重建完成，文档已可检索")


@KnowledgeRouter.delete(
    "/document/delete",
    summary="Delete knowledge documents",
    response_model=ResponseSchema[None],
)
async def delete_document_controller(
    ids: Annotated[list[int], Body()],
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:document:delete"]))],
) -> JSONResponse:
    await KnowledgeDocumentLifecycle(auth).delete_document(ids=ids)
    return SuccessResponse(msg="delete knowledge document success")


@KnowledgeRouter.post(
    "/retrieval/test",
    summary="Test knowledge retrieval",
    response_model=ResponseSchema[dict[str, Any]],
)
async def retrieval_test_controller(
    data: RetrievalTestSchema,
    auth: Annotated[AuthSchema, Depends(AuthPermission(["module_ai:retrieval:test"]))],
) -> JSONResponse:
    result = await KnowledgeService(auth).query_retrieval(data=data)
    return SuccessResponse(data=result, msg="test retrieval success")
