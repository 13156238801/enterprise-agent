"""提供主数据快照、patch 与结构化差异的纯领域规则。"""

from app.domain.master_data.diff import (
    JsonValue,
    MasterDataDiffItem,
    MasterDataDiffOperation,
    apply_master_data_patch,
    build_master_data_diff,
    validate_entity_identity,
    validate_json_object,
)
from app.domain.master_data.errors import MasterDataDomainError, MasterDataDomainErrorCode

__all__ = [
    "JsonValue",
    "MasterDataDiffItem",
    "MasterDataDiffOperation",
    "MasterDataDomainError",
    "MasterDataDomainErrorCode",
    "apply_master_data_patch",
    "build_master_data_diff",
    "validate_entity_identity",
    "validate_json_object",
]
