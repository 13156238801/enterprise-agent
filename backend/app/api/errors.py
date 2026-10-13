from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.auth.errors import AuthErrorCode
from app.core.errors import AppError, ErrorEnvelope, ErrorInfo
from app.master_data.errors import MasterDataErrorCode

_AUTH_HTTP_STATUS = {
    AuthErrorCode.INVALID_CREDENTIALS.value: 401,
    AuthErrorCode.UNAUTHENTICATED.value: 401,
    AuthErrorCode.TOKEN_EXPIRED.value: 401,
    AuthErrorCode.USER_DISABLED.value: 403,
    AuthErrorCode.PERMISSION_DENIED.value: 403,
}
_MASTER_DATA_HTTP_STATUS = {
    MasterDataErrorCode.NOT_FOUND.value: 404,
    MasterDataErrorCode.INVALID_CHANGE.value: 422,
    MasterDataErrorCode.NO_CHANGE.value: 422,
    MasterDataErrorCode.DRAFT_NOT_FOUND.value: 404,
    MasterDataErrorCode.DRAFT_NOT_EDITABLE.value: 409,
    MasterDataErrorCode.DRAFT_FORBIDDEN.value: 403,
    MasterDataErrorCode.DRAFT_ENTITY_MISMATCH.value: 409,
}


async def app_error_handler(request: Request, error: AppError) -> JSONResponse:
    """仅在 HTTP 边界把应用错误映射为统一 Envelope 和状态码。"""
    status_code = _AUTH_HTTP_STATUS.get(error.code, _MASTER_DATA_HTTP_STATUS.get(error.code, 500))
    headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
    request_id = getattr(request.state, "request_id", "")
    envelope = ErrorEnvelope(
        error=ErrorInfo(
            code=error.code,
            message=error.message,
            request_id=request_id,
            retryable=error.retryable,
            details=error.details,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers=headers,
    )


def register_error_handlers(api: FastAPI) -> None:
    """为 FastAPI 应用注册统一应用错误处理器。"""
    api.add_exception_handler(AppError, app_error_handler)
