import json
import logging

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.logging import configure_logging, json_formatter
from app.core.request_context import request_id_var
from app.main import app


def test_logging_formatter_includes_request_id_and_standard_fields() -> None:
    token = request_id_var.set("format-test-id")
    try:
        record = logging.LogRecord("sample.logger", logging.INFO, "", 0, "hello", (), None)
        rendered = json.loads(json_formatter.format(record))
    finally:
        request_id_var.reset(token)

    assert rendered["level"] == "INFO"
    assert rendered["logger"] == "sample.logger"
    assert rendered["message"] == "hello"
    assert rendered["request_id"] == "format-test-id"
    assert "timestamp" in rendered
    assert "exception" not in rendered


def test_logging_formatter_preserves_exception_traceback(caplog) -> None:
    logger = logging.getLogger("sample.exception")
    with caplog.at_level(logging.ERROR, logger="sample.exception"):
        try:
            raise ValueError("controlled test exception")
        except ValueError:
            logger.exception("operation failed")

    record = next(record for record in caplog.records if record.name == "sample.exception")
    rendered = json.loads(json_formatter.format(record))

    assert "exception" in rendered
    assert "ValueError: controlled test exception" in rendered["exception"]


def test_logging_initialization_is_idempotent(caplog) -> None:
    settings = Settings(_env_file=None)
    root_logger = logging.getLogger()
    before = len(root_logger.handlers)
    configure_logging(settings)
    configure_logging(settings)
    after = len(root_logger.handlers)
    caplog.set_level(logging.INFO, logger="enterprise_agent.access")

    response = TestClient(app).get("/health", headers={"X-Request-ID": "once-only"})

    records = [record for record in caplog.records if record.name == "enterprise_agent.access"]
    assert response.status_code == 200
    assert after == before
    assert len(records) == 1
    assert records[0].request_id == "once-only"
    assert logging.getLogger("uvicorn.access").disabled


def test_access_log_does_not_include_sensitive_request_data(caplog) -> None:
    caplog.set_level(logging.INFO, logger="enterprise_agent.access")

    response = TestClient(app).post(
        "/missing?api_key=query-secret",
        headers={
            "X-Request-ID": "safe-id",
            "Authorization": "Bearer authorization-secret",
            "Cookie": "session=cookie-secret",
        },
        content="password=body-secret",
    )

    assert response.status_code == 404
    access_records = [
        record for record in caplog.records if record.name == "enterprise_agent.access"
    ]
    assert len(access_records) == 1
    rendered_access_log = json_formatter.format(access_records[0])
    for secret in ("query-secret", "authorization-secret", "cookie-secret", "body-secret"):
        assert secret not in rendered_access_log
