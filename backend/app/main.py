from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.request_context import RequestIdMiddleware

settings = get_settings()
configure_logging(settings)

api = FastAPI(title="Enterprise Agent API", version="0.1.0")


@api.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# Keep the request middleware outside FastAPI's ServerErrorMiddleware so generated 500s
# pass through its response-header and access-log handling too.
app = RequestIdMiddleware(api, settings)
