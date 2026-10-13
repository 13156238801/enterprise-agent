"""提供经过认证的主数据查询和 APPLICANT 草稿 API。"""

from fastapi import APIRouter, Depends

from app.api.deps import get_current_auth, get_master_data_service, require_any_role
from app.auth.context import AuthContext
from app.domain.auth.enums import UserRole
from app.master_data.schemas import (
    ChangeDraftRequest,
    ChangeDraftResponse,
    MasterDataEntityResponse,
)
from app.master_data.service import MasterDataService

router = APIRouter(prefix="/api/v1/master-data", tags=["master-data"])


@router.get("/{entity_type}/{entity_key}", response_model=MasterDataEntityResponse)
async def get_master_data(
    entity_type: str,
    entity_key: str,
    auth_context: AuthContext = Depends(get_current_auth),
    service: MasterDataService = Depends(get_master_data_service),
) -> MasterDataEntityResponse:
    """返回当前实体快照；认证身份只用于建立已认证访问边界。"""
    return await service.get_entity(entity_type, entity_key)


@router.post(
    "/{entity_type}/{entity_key}/changes",
    response_model=ChangeDraftResponse,
)
async def create_or_update_change_draft(
    entity_type: str,
    entity_key: str,
    payload: ChangeDraftRequest,
    auth_context: AuthContext = Depends(require_any_role(UserRole.APPLICANT)),
    service: MasterDataService = Depends(get_master_data_service),
) -> ChangeDraftResponse:
    """由 APPLICANT 创建或显式更新草稿，身份从服务端 AuthContext 注入。"""
    return await service.create_or_update_draft(
        entity_type,
        entity_key,
        payload,
        auth_context,
    )
