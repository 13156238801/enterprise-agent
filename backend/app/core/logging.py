import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings, get_settings
from app.core.request_context import request_id_var

_HANDLER_MARKER = "_enterprise_agent_handler"


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get() or "-"
        return True


class JsonFormatter(logging.Formatter):
    _ACCESS_FIELDS = ("method", "path", "status_code", "duration_ms")

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None) or request_id_var.get() or "-",
        }
        for field in self._ACCESS_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


json_formatter = JsonFormatter()


def configure_logging(settings: Settings | None = None) -> None:
    resolved_settings = settings or get_settings()
    root_logger = logging.getLogger()
    root_logger.setLevel(resolved_settings.log_level)

    handler = next(
        (item for item in root_logger.handlers if getattr(item, _HANDLER_MARKER, False)),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler(sys.stdout)
        setattr(handler, _HANDLER_MARKER, True)
        handler.addFilter(RequestIdFilter())
        root_logger.addHandler(handler)
    handler.setLevel(resolved_settings.log_level)
    handler.setFormatter(json_formatter)

    access_logger = logging.getLogger("enterprise_agent.access")
    access_logger.setLevel(resolved_settings.log_level)
    if not any(isinstance(item, RequestIdFilter) for item in access_logger.filters):
        access_logger.addFilter(RequestIdFilter())

    # Uvicorn's default access formatter includes the raw target, which may contain query secrets.
    logging.getLogger("uvicorn.access").disabled = True
