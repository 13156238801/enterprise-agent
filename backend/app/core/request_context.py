import logging
import re
import time
from contextvars import ContextVar
from uuid import uuid4

from app.core.config import Settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z", re.ASCII)
_access_logger = logging.getLogger("enterprise_agent.access")


def _valid_request_id(value: bytes) -> str | None:
    try:
        decoded = value.decode("ascii")
    except UnicodeDecodeError:
        return None
    if _REQUEST_ID_PATTERN.fullmatch(decoded):
        return decoded
    return None


class RequestIdMiddleware:
    """Pure ASGI middleware for request ID propagation and safe access logs."""

    def __init__(self, app, settings: Settings) -> None:
        self.app = app
        self.header_name = settings.request_id_header.encode("ascii").lower()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = next(
            (
                candidate
                for name, value in scope.get("headers", [])
                if name.lower() == self.header_name
                for candidate in [_valid_request_id(value)]
                if candidate is not None
            ),
            None,
        ) or str(uuid4())

        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        token = request_id_var.set(request_id)
        start_time = time.perf_counter()
        status_code = 500
        response_header_name = self.header_name

        async def send_with_request_id(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != response_header_name
                ]
                headers.append((response_header_name, request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000
            _access_logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "method": scope.get("method", ""),
                    "path": scope.get("path", ""),
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            request_id_var.reset(token)
