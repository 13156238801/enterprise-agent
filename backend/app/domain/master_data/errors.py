"""定义主数据纯领域校验使用的稳定错误。"""

from enum import StrEnum


class MasterDataDomainErrorCode(StrEnum):
    """列举主数据标识、快照和 patch 校验错误码。"""

    INVALID_ENTITY_TYPE = "MASTER_DATA_INVALID_ENTITY_TYPE"
    INVALID_ENTITY_KEY = "MASTER_DATA_INVALID_ENTITY_KEY"
    DATA_NOT_OBJECT = "MASTER_DATA_DATA_NOT_OBJECT"
    DATA_NOT_JSON_COMPATIBLE = "MASTER_DATA_DATA_NOT_JSON_COMPATIBLE"
    INVALID_CHANGE = "MASTER_DATA_INVALID_CHANGE"


class MasterDataDomainError(Exception):
    """携带稳定代码和领域校验说明的主数据异常。"""

    def __init__(self, code: MasterDataDomainErrorCode, message: str) -> None:
        """保存机器可读错误码及内部诊断说明。"""
        self.code = code
        super().__init__(message)
