"""定义审批操作 API 的请求与安全响应模型。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.approval.enums import ApprovalAction, ApprovalStatus, BusinessType
from app.domain.auth.enums import UserRole


class SubmitApprovalResponse(BaseModel):
    """返回已提交申请的绑定流程和首节点运行时信息。"""

    model_config = ConfigDict(extra="forbid")

    approval_id: UUID
    status: ApprovalStatus
    workflow_code: str
    workflow_version: int
    workflow_instance_id: str
    current_task_id: str
    current_node_code: str
    current_node_role: UserRole
    submitted_at: datetime
    version: int


class SubmitApprovalRequest(BaseModel):
    """接收提交动作的 expected version，缺失值交由 Service 稳定映射。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: Any = None


class ApproveRequest(BaseModel):
    """接收可选审批意见，业务类型校验由 Service 统一处理。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: Any = None
    comment: object | None = None


class RejectRequest(BaseModel):
    """接收驳回目标和意见，业务字段由 Service 映射稳定错误。"""

    model_config = ConfigDict(extra="forbid")

    target: object
    comment: object
    expected_version: Any = None


class WithdrawRequest(BaseModel):
    """接收可选撤回意见，不接受客户端指定身份或流程运行时字段。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: Any = None
    comment: object | None = None


class ApprovalActionResponse(BaseModel):
    """返回审批动作结果和当前流程运行时，不暴露目标处理人。"""

    model_config = ConfigDict(extra="forbid")

    approval_id: UUID
    action: ApprovalAction
    status: ApprovalStatus
    workflow_code: str
    workflow_version: int
    workflow_instance_id: str
    current_task_id: str | None
    current_node_code: str | None
    current_node_role: UserRole | None
    completed_at: datetime | None
    version: int


class ApprovalListItem(BaseModel):
    """定义审批列表摘要，不包含内部 task 或目标处理人信息。"""

    model_config = ConfigDict(extra="forbid")

    approval_id: UUID
    business_type: BusinessType
    status: ApprovalStatus
    applicant_id: str
    entity_type: str
    entity_key: str
    change_reason: str | None
    workflow_code: str | None
    workflow_version: int | None
    current_node_code: str | None
    current_node_role: UserRole | None
    submitted_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class ApprovalListResponse(BaseModel):
    """表示带 offset 分页元数据的审批列表。"""

    model_config = ConfigDict(extra="forbid")

    items: list[ApprovalListItem]
    limit: int
    offset: int
    has_more: bool


class MasterDataChangeDetail(BaseModel):
    """返回草稿持久化的主数据快照与结构化差异。"""

    model_config = ConfigDict(extra="forbid")

    change_id: UUID
    entity_type: str
    entity_key: str
    before_data: dict[str, object]
    form_data: dict[str, object]
    change_items: list[dict[str, object]]
    change_reason: str | None
    base_entity_version: int
    created_at: datetime
    updated_at: datetime


class ApprovalDetailResponse(BaseModel):
    """定义审批详情和结构化变更内容，不暴露 current task ID。"""

    model_config = ConfigDict(extra="forbid")

    approval_id: UUID
    business_type: BusinessType
    business_id: str
    applicant_id: str
    status: ApprovalStatus
    workflow_code: str | None
    workflow_version: int | None
    workflow_instance_id: str | None
    current_node_code: str | None
    current_node_role: UserRole | None
    submitted_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int
    change: MasterDataChangeDetail


class ApprovalHistoryItem(BaseModel):
    """定义可安全公开的审批历史快照，不包含内部 task 分配字段。"""

    model_config = ConfigDict(extra="forbid")

    record_id: UUID
    operator_id: str
    action: ApprovalAction
    comment: str | None
    from_status: ApprovalStatus | None
    to_status: ApprovalStatus
    from_node_code: str | None
    from_node_name: str | None
    target_node_code: str | None
    target_node_name: str | None
    created_at: datetime


class ApprovalHistoryResponse(BaseModel):
    """封装按时间正序排列的只读审批历史。"""

    model_config = ConfigDict(extra="forbid")

    items: list[ApprovalHistoryItem]
