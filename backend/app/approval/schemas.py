"""定义审批提交 API 的安全响应模型。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.approval.enums import ApprovalStatus
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
