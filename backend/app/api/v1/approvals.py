"""提供经过认证的审批提交端点。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query

from app.api.deps import get_approval_service, get_current_auth, require_any_role
from app.approval.schemas import (
    ApprovalActionResponse,
    ApprovalDetailResponse,
    ApprovalHistoryResponse,
    ApprovalListResponse,
    ApproveRequest,
    RejectRequest,
    SubmitApprovalRequest,
    SubmitApprovalResponse,
    WithdrawRequest,
)
from app.approval.service import ApprovalService
from app.auth.context import AuthContext
from app.domain.approval.enums import ApprovalStatus
from app.domain.auth.enums import UserRole

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


@router.get("", response_model=ApprovalListResponse)
async def list_my_approvals(
    status: ApprovalStatus | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth_context: AuthContext = Depends(get_current_auth),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalListResponse:
    """列出当前认证申请人发起的审批并应用有限 offset 分页。"""
    return await service.list_my_approvals(auth_context, status=status, limit=limit, offset=offset)


@router.get("/pending", response_model=ApprovalListResponse)
async def list_pending_approvals(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    auth_context: AuthContext = Depends(require_any_role(UserRole.APPROVER, UserRole.DATA_ADMIN)),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalListResponse:
    """列出当前 reviewer 有资格处理的待办审批。"""
    return await service.list_pending_approvals(auth_context, limit=limit, offset=offset)


@router.get("/{approval_id}/history", response_model=ApprovalHistoryResponse)
async def get_approval_history(
    approval_id: UUID,
    auth_context: AuthContext = Depends(get_current_auth),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalHistoryResponse:
    """读取通过 Service 数据权限校验的审批动作历史。"""
    return await service.get_history(approval_id, auth_context)


@router.get("/{approval_id}", response_model=ApprovalDetailResponse)
async def get_approval_detail(
    approval_id: UUID,
    auth_context: AuthContext = Depends(get_current_auth),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalDetailResponse:
    """读取通过 Service 数据权限校验的审批详情和主数据快照。"""
    return await service.get_detail(approval_id, auth_context)


@router.post("/{approval_id}/submit", response_model=SubmitApprovalResponse)
async def submit_approval(
    approval_id: UUID,
    payload: SubmitApprovalRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth_context: AuthContext = Depends(require_any_role(UserRole.APPLICANT)),
    service: ApprovalService = Depends(get_approval_service),
) -> SubmitApprovalResponse:
    """提交由路径明确指定的草稿，忽略客户端不需要的业务请求体。"""
    return await service.submit(
        approval_id,
        auth_context,
        payload.expected_version if payload else None,
        idempotency_key,
    )


@router.post("/{approval_id}/approve", response_model=ApprovalActionResponse)
async def approve_approval(
    approval_id: UUID,
    payload: ApproveRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth_context: AuthContext = Depends(require_any_role(UserRole.APPROVER, UserRole.DATA_ADMIN)),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalActionResponse:
    """执行已认证审批人的 approve 操作并返回安全运行时视图。"""
    return await service.approve(
        approval_id,
        auth_context,
        payload.expected_version if payload else None,
        payload.comment if payload else None,
        idempotency_key,
    )


@router.post("/{approval_id}/reject", response_model=ApprovalActionResponse)
async def reject_approval(
    approval_id: UUID,
    payload: RejectRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth_context: AuthContext = Depends(require_any_role(UserRole.APPROVER, UserRole.DATA_ADMIN)),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalActionResponse:
    """执行已认证审批人的 reject 操作，不接受客户端指定节点或处理人。"""
    return await service.reject(
        approval_id,
        auth_context,
        payload.target,
        payload.comment,
        payload.expected_version,
        idempotency_key,
    )


@router.post("/{approval_id}/withdraw", response_model=ApprovalActionResponse)
async def withdraw_approval(
    approval_id: UUID,
    payload: WithdrawRequest | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    auth_context: AuthContext = Depends(require_any_role(UserRole.APPLICANT)),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalActionResponse:
    """撤回当前申请人的审批申请，不接受客户端提供申请人身份。"""
    return await service.withdraw(
        approval_id,
        auth_context,
        payload.expected_version if payload else None,
        payload.comment if payload else None,
        idempotency_key,
    )
