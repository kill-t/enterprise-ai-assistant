from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Response, UploadFile, status

from app.modules.identity.dependencies import CurrentUserDep
from app.modules.knowledge.dependencies import DocumentServiceDep
from app.modules.knowledge.schema.document_schema import (
    DocumentResponse,
    DocumentRetryResponse,
    DocumentUploadResponse,
)


knowledge_base_documents_router = APIRouter(
    prefix="/api/v1/knowledge-bases",
    tags=["知识库文档"],
)
document_router = APIRouter(
    prefix="/api/v1/documents",
    tags=["知识库文档"],
)


@knowledge_base_documents_router.post(
    "/{knowledge_base_id}/documents",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    knowledge_base_id: UUID,
    file: Annotated[
        UploadFile,
        File(description="支持 PDF、TXT、Markdown，单文件最大 20 MB"),
    ],
    service: DocumentServiceDep,
    current_user: CurrentUserDep,
) -> DocumentUploadResponse:
    """接收原文件，并创建可恢复的持久化入库任务。"""
    return await service.upload(
        user_id=current_user.id,
        knowledge_base_id=knowledge_base_id,
        upload=file,
    )


@knowledge_base_documents_router.get(
    "/{knowledge_base_id}/documents",
    response_model=list[DocumentResponse],
)
async def list_documents(
    knowledge_base_id: UUID,
    service: DocumentServiceDep,
    current_user: CurrentUserDep,
) -> list[DocumentResponse]:
    """返回当前用户知识库中的文档及入库状态。"""
    return await service.list_documents(
        user_id=current_user.id,
        knowledge_base_id=knowledge_base_id,
    )


@document_router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: UUID,
    service: DocumentServiceDep,
    current_user: CurrentUserDep,
) -> DocumentResponse:
    """返回当前用户拥有的指定文档状态。"""
    return await service.get_document(
        user_id=current_user.id,
        document_id=document_id,
    )


@document_router.post(
    "/{document_id}/retry",
    response_model=DocumentRetryResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_document(
    document_id: UUID,
    service: DocumentServiceDep,
    current_user: CurrentUserDep,
) -> DocumentRetryResponse:
    """为当前用户的失败文档创建新入库任务。"""
    return await service.retry_document(
        user_id=current_user.id,
        document_id=document_id,
    )


@document_router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    document_id: UUID,
    service: DocumentServiceDep,
    current_user: CurrentUserDep,
) -> Response:
    """逻辑删除当前用户拥有且不再执行入库任务的文档。"""
    await service.delete_document(
        user_id=current_user.id,
        document_id=document_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
