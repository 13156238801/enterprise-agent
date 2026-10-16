"""提供经过认证的审批提交端点。"""

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.deps import get_approval_service, require_any_role
from app.approval.schemas import (
    ApprovalActionResponse,
    ApproveRequest,
    RejectRequest,
    SubmitApprovalResponse,
    WithdrawRequest,
)
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


@router.post("/{approval_id}/approve", response_model=ApprovalActionResponse)
async def approve_approval(
    approval_id: UUID,
    payload: ApproveRequest | None = None,
    auth_context: AuthContext = Depends(require_any_role(UserRole.APPROVER, UserRole.DATA_ADMIN)),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalActionResponse:
    """执行已认证审批人的 approve 操作并返回安全运行时视图。"""
    return await service.approve(approval_id, auth_context, payload.comment if payload else None)


@router.post("/{approval_id}/reject", response_model=ApprovalActionResponse)
async def reject_approval(
    approval_id: UUID,
    payload: RejectRequest,
    auth_context: AuthContext = Depends(require_any_role(UserRole.APPROVER, UserRole.DATA_ADMIN)),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalActionResponse:
    """执行已认证审批人的 reject 操作，不接受客户端指定节点或处理人。"""
    return await service.reject(approval_id, auth_context, payload.target, payload.comment)


@router.post("/{approval_id}/withdraw", response_model=ApprovalActionResponse)
async def withdraw_approval(
    approval_id: UUID,
    payload: WithdrawRequest | None = None,
    auth_context: AuthContext = Depends(require_any_role(UserRole.APPLICANT)),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalActionResponse:
    """撤回当前申请人的审批申请，不接受客户端提供申请人身份。"""
    return await service.withdraw(approval_id, auth_context, payload.comment if payload else None)
