from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorInfo(BaseModel):
    """表示稳定的 API 错误信息，不包含底层异常详情。"""

    code: str
    message: str
    request_id: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    """封装项目统一的 HTTP 错误响应结构。"""

    model_config = ConfigDict(extra="forbid")

    error: ErrorInfo


class AppError(Exception):
    """保存可安全映射至 API 的应用错误码和公开消息。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        """初始化稳定错误字段；details 只允许包含可公开数据。"""
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
