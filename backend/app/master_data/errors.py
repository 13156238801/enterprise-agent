"""定义可由主数据 API 安全映射的稳定应用错误。"""

from enum import StrEnum

from app.core.errors import AppError


class MasterDataErrorCode(StrEnum):
    """列举实体、变更 patch 与草稿操作的稳定错误码。"""

    NOT_FOUND = "MASTER_DATA_NOT_FOUND"
    INVALID_CHANGE = "MASTER_DATA_INVALID_CHANGE"
    NO_CHANGE = "MASTER_DATA_NO_CHANGE"
    DRAFT_NOT_FOUND = "MASTER_DATA_DRAFT_NOT_FOUND"
    DRAFT_NOT_EDITABLE = "MASTER_DATA_DRAFT_NOT_EDITABLE"
    DRAFT_FORBIDDEN = "MASTER_DATA_DRAFT_FORBIDDEN"
    DRAFT_ENTITY_MISMATCH = "MASTER_DATA_DRAFT_ENTITY_MISMATCH"
    VERSION_CONFLICT = "MASTER_DATA_VERSION_CONFLICT"


_MESSAGES = {
    MasterDataErrorCode.NOT_FOUND: "主数据实体不存在",
    MasterDataErrorCode.INVALID_CHANGE: "主数据变更请求不合法",
    MasterDataErrorCode.NO_CHANGE: "变更内容与当前草稿没有差异",
    MasterDataErrorCode.DRAFT_NOT_FOUND: "主数据变更草稿不存在",
    MasterDataErrorCode.DRAFT_NOT_EDITABLE: "当前草稿状态不允许修改",
    MasterDataErrorCode.DRAFT_FORBIDDEN: "只有原申请人可以修改该草稿",
    MasterDataErrorCode.DRAFT_ENTITY_MISMATCH: "路由实体与草稿实体不一致",
    MasterDataErrorCode.VERSION_CONFLICT: "主数据或草稿版本已变化，请刷新后重试",
}


class MasterDataError(AppError):
    """封装主数据操作的稳定错误码和安全公开消息。"""

    def __init__(self, code: MasterDataErrorCode) -> None:
        """按错误码设置固定公开消息，不携带底层输入或异常详情。"""
        super().__init__(code.value, _MESSAGES[code])
