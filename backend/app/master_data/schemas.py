"""定义主数据 API 输入、实体响应和草稿响应模型。"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.approval.enums import ApprovalStatus
from app.domain.master_data.diff import (
    JsonValue,
    MasterDataDiffOperation,
)


class ChangeDraftRequest(BaseModel):
    """验证创建或更新草稿的 patch、reason 和可选显式草稿 ID。"""

    model_config = ConfigDict(extra="forbid")

    draft_approval_id: UUID | None = None
    expected_version: Any = None
    # 将业务校验留给 Service，确保错误经过统一 AppError Envelope。
    changes: Any
    reason: Any


class MasterDataEntityResponse(BaseModel):
    """定义当前主数据读取 API 的公开实体快照。"""

    id: UUID
    entity_type: str
    entity_key: str
    data: dict[str, JsonValue]
    version: int


class MasterDataDiffItemResponse(BaseModel):
    """定义 API 中单个结构化主数据差异。"""

    field: str
    operation: MasterDataDiffOperation
    before: JsonValue
    after: JsonValue


class ChangeDraftResponse(BaseModel):
    """定义创建或更新草稿返回的完整 before/after/diff 视图。"""

    approval_id: UUID
    approval_version: int
    change_id: UUID
    status: ApprovalStatus
    entity_type: str
    entity_key: str
    base_entity_version: int
    before: dict[str, JsonValue]
    after: dict[str, JsonValue]
    diff: list[MasterDataDiffItemResponse]
    reason: str
