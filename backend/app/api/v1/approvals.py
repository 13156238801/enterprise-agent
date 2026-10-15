"""提供经过认证的审批提交端点。"""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import get_approval_service, require_any_role
from app.approval.schemas import SubmitApprovalResponse
from app.approval.service import ApprovalService
from app.auth.context import AuthContext
from app.domain.auth.enums import UserRole

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


@router.post("/{approval_id}/submit", response_model=SubmitApprovalResponse)
async def submit_approval(
    approval_id: UUID,
    auth_context: AuthContext = Depends(require_any_role(UserRole.APPLICANT)),
    service: ApprovalService = Depends(get_approval_service),
) -> SubmitApprovalResponse:
    """提交由路径明确指定的草稿，忽略客户端不需要的业务请求体。"""
    return await service.submit(approval_id, auth_context)
