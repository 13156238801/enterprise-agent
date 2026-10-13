"""定义主数据 API 输入、实体响应和草稿响应模型。"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.approval.enums import ApprovalStatus
from app.domain.master_data.diff import (
    JsonValue,
    MasterDataDiffOperation,
    validate_json_object,
)
from app.domain.master_data.errors import MasterDataDomainError


class ChangeDraftRequest(BaseModel):
    """验证创建或更新草稿的 patch、reason 和可选显式草稿 ID。"""

    model_config = ConfigDict(extra="forbid")

    draft_approval_id: UUID | None = None
    changes: dict[str, JsonValue]
    reason: str = Field(max_length=2000)

    @field_validator("changes", mode="before")
    @classmethod
    def validate_changes(cls, value: object) -> dict[str, Any]:
        """在 Pydantic 类型转换前验证 patch 是 JSON-compatible object。"""
        try:
            return validate_json_object(value, "changes")
        except MasterDataDomainError as error:
            raise ValueError(str(error)) from error

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        """去除 reason 首尾空格并拒绝空白说明。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason 去除首尾空格后不能为空")
        if len(normalized) > 2000:
            raise ValueError("reason 长度不能超过 2000")
        return normalized


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
    change_id: UUID
    status: ApprovalStatus
    entity_type: str
    entity_key: str
    base_entity_version: int
    before: dict[str, JsonValue]
    after: dict[str, JsonValue]
    diff: list[MasterDataDiffItemResponse]
    reason: str
