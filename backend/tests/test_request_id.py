import asyncio
import logging
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.logging import json_formatter
from app.core.request_context import RequestIdMiddleware, request_id_var
from app.main import app

client = TestClient(app)


def test_server_generates_request_id() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert re.fullmatch(r"[0-9a-f-]{36}", response.headers["X-Request-ID"])


def test_client_request_id_is_propagated() -> None:
    response = client.get("/health", headers={"X-Request-ID": "manual-test-001"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "manual-test-001"


def test_request_id_accepts_allowed_characters_at_maximum_length() -> None:
    request_id = "Aa09-_.:" + "x" * 120

    response = client.get("/health", headers={"X-Request-ID": request_id})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id


@pytest.mark.parametrize(
    "request_id",
    ["", "x" * 129, "bad/id", "bad\r\nInjected: yes", "space not allowed"],
)
def test_invalid_request_id_is_replaced(request_id: str) -> None:
    response = client.get("/health", headers={"X-Request-ID": request_id})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != request_id
    assert re.fullmatch(r"[0-9a-f-]{36}", response.headers["X-Request-ID"])


def test_request_ids_are_isolated() -> None:
    first = client.get("/health", headers={"X-Request-ID": "request-one"})
    second = client.get("/health", headers={"X-Request-ID": "request-two"})

    assert first.headers["X-Request-ID"] == "request-one"
    assert second.headers["X-Request-ID"] == "request-two"


@pytest.mark.parametrize("request_id", [None, "server-error-id"])
def test_uncaught_server_error_response_has_request_id(request_id: str | None) -> None:
    error_app = FastAPI()

    @error_app.get("/boom")
    def raise_uncaught_error() -> None:
        raise RuntimeError("expected test failure")

    headers = {"X-Request-ID": request_id} if request_id else {}
    request_id_app = RequestIdMiddleware(error_app, Settings(_env_file=None))
    response = TestClient(request_id_app, raise_server_exceptions=False).get(
        "/boom", headers=headers
    )

    assert response.status_code == 500
    assert response.headers.get("X-Request-ID")
    if request_id:
        assert response.headers["X-Request-ID"] == request_id


def test_not_found_has_request_id_and_access_log(caplog) -> None:
    caplog.set_level(logging.INFO, logger="enterprise_agent.access")

    response = client.get("/missing?token=query-secret", headers={"X-Request-ID": "not-found-id"})

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "not-found-id"
    records = [record for record in caplog.records if record.name == "enterprise_agent.access"]
    assert len(records) == 1
    assert records[0].request_id == "not-found-id"
    assert records[0].method == "GET"
    assert records[0].path == "/missing"
    assert records[0].status_code == 404
    assert records[0].duration_ms >= 0
    assert "query-secret" not in json_formatter.format(records[0])


def test_request_state_and_contextvar_are_set_and_reset() -> None:
    observed: dict[str, str | None] = {}

    async def application(scope, receive, send) -> None:
        observed["state"] = scope["state"]["request_id"]
        observed["context"] = request_id_var.get()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def run_request() -> None:
        middleware = RequestIdMiddleware(application, Settings(_env_file=None))
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/probe",
            "raw_path": b"/probe",
            "query_string": b"",
            "headers": [(b"x-request-id", b"context-id")],
            "state": {},
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "http_version": "1.1",
        }
        sent = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await middleware(scope, receive, send)
        assert request_id_var.get() is None
        response_start = next(
            message for message in sent if message["type"] == "http.response.start"
        )
        assert (b"x-request-id", b"context-id") in response_start["headers"]

    asyncio.run(run_request())
    assert observed == {"state": "context-id", "context": "context-id"}
