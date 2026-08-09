from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.modules.identity.dependencies import CurrentUserDep
from app.modules.knowledge.dependencies import KnowledgeBaseServiceDep
from app.modules.knowledge.schema.knowledge_base_schema import (
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
)


router = APIRouter(
    prefix="/api/v1/knowledge-bases",
    tags=["知识库"],
)


@router.post(
    "",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_base(
    request: KnowledgeBaseCreate,
    service: KnowledgeBaseServiceDep,
    current_user: CurrentUserDep,
) -> KnowledgeBaseResponse:
    """为当前登录用户创建私有知识库。"""
    return await service.create(user_id=current_user.id, request=request)


@router.get("", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases(
    service: KnowledgeBaseServiceDep,
    current_user: CurrentUserDep,
    include_archived: bool = Query(default=False),
) -> list[KnowledgeBaseResponse]:
    """返回当前用户的知识库，并可选择包含已归档记录。"""
    return await service.list_knowledge_bases(
        user_id=current_user.id,
        include_archived=include_archived,
    )


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def get_knowledge_base(
    knowledge_base_id: UUID,
    service: KnowledgeBaseServiceDep,
    current_user: CurrentUserDep,
) -> KnowledgeBaseResponse:
    """返回当前用户拥有的指定知识库。"""
    return await service.get(
        user_id=current_user.id,
        knowledge_base_id=knowledge_base_id,
    )


@router.patch("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
async def update_knowledge_base(
    knowledge_base_id: UUID,
    request: KnowledgeBaseUpdate,
    service: KnowledgeBaseServiceDep,
    current_user: CurrentUserDep,
) -> KnowledgeBaseResponse:
    """修改当前用户知识库的名称、描述或归档状态。"""
    return await service.update(
        user_id=current_user.id,
        knowledge_base_id=knowledge_base_id,
        changes=request,
    )


@router.delete("/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_knowledge_base(
    knowledge_base_id: UUID,
    service: KnowledgeBaseServiceDep,
    current_user: CurrentUserDep,
) -> Response:
    """将当前用户的知识库软归档，后续可通过 PATCH 恢复。"""
    await service.archive(
        user_id=current_user.id,
        knowledge_base_id=knowledge_base_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
