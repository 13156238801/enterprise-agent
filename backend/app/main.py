import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.request_context import RequestIdMiddleware
from app.infrastructure.database import create_db_engine, create_session_factory
from app.infrastructure.redis import create_redis_client

settings = get_settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def _close_resources(api: FastAPI) -> None:
    """尽力释放应用状态中的 Redis 与数据库资源，并隔离单项清理失败。"""
    redis = getattr(api.state, "redis", None)
    engine = getattr(api.state, "db_engine", None)
    # 两类资源分别释放；某一方失败不能跳过另一方的 cleanup。
    if redis is not None:
        try:
            await redis.aclose()
        except Exception:
            logger.exception("Failed to close Redis client")
    if engine is not None:
        try:
            await engine.dispose()
        except Exception:
            logger.exception("Failed to dispose database engine")


def create_app(app_settings=settings) -> FastAPI:
    """创建并配置 FastAPI 应用；参数提供该应用使用的运行时设置。"""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        """在应用启动时创建共享资源，并在退出时尝试释放已创建资源。"""
        # lifespan 与 FastAPI app 同生命周期，集中创建并释放 worker 级共享 I/O 资源。
        try:
            application.state.db_engine = create_db_engine(app_settings)
            application.state.db_session_factory = create_session_factory(
                application.state.db_engine
            )
            application.state.redis = create_redis_client(app_settings)
            yield
        finally:
            await _close_resources(application)

    api = FastAPI(title="Enterprise Agent API", version="0.1.0", lifespan=lifespan)

    @api.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        """返回不依赖外部服务的存活状态。"""
        # Liveness 只判断进程是否可响应，避免依赖故障触发容器重启风暴。
        return {"status": "ok"}

    @api.get("/ready", tags=["health"])
    async def readiness(request: Request) -> JSONResponse:
        """并发探测已初始化的数据库和 Redis 资源，失败时仅返回稳定状态。"""

        async def check_postgres() -> str:
            """限时执行 PostgreSQL SELECT 1，返回 ok 或 error。"""
            try:
                async with asyncio.timeout(2):
                    async with request.app.state.db_engine.connect() as connection:
                        await connection.execute(text("SELECT 1"))
                return "ok"
            except Exception:
                return "error"

        async def check_redis() -> str:
            """限时执行 Redis PING，返回 ok 或 error。"""
            try:
                async with asyncio.timeout(2):
                    await request.app.state.redis.ping()
                return "ok"
            except Exception:
                return "error"

        # 并发探测将 readiness 最长耗时控制在单个依赖 timeout 附近。
        postgres_status, redis_status = await asyncio.gather(check_postgres(), check_redis())
        is_ready = postgres_status == redis_status == "ok"
        return JSONResponse(
            status_code=200 if is_ready else 503,
            content={
                "status": "ready" if is_ready else "not_ready",
                "postgres": postgres_status,
                "redis": redis_status,
            },
        )

    return api


# 将 RequestIdMiddleware 放在最外层，才能覆盖 FastAPI ServerErrorMiddleware
# 生成的 500 响应，确保错误响应也带 Request ID 并记录 access log。
api = create_app(settings)
app = RequestIdMiddleware(api, settings)
