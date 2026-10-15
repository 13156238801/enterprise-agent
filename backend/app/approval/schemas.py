"""定义审批操作 API 的请求与安全响应模型。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.approval.enums import ApprovalAction, ApprovalStatus
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


class ApproveRequest(BaseModel):
    """接收可选审批意见，业务类型校验由 Service 统一处理。"""

    model_config = ConfigDict(extra="forbid")

    comment: object | None = None


class RejectRequest(BaseModel):
    """接收驳回目标和意见，业务字段由 Service 映射稳定错误。"""

    model_config = ConfigDict(extra="forbid")

    target: object
    comment: object


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
