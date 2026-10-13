"""确认 P1-004 downgrade 只移除工作流表。"""

import asyncio

from sqlalchemy import inspect

from app.core.config import get_settings
from app.infrastructure.database import create_db_engine

_REQUIRED_TABLES = {
    "approval_application",
    "approval_record",
    "master_data_change",
    "users",
    "user_roles",
    "auth_sessions",
}


async def main() -> None:
    """检查当前数据库处于 P1-004 downgrade 后的预期表状态。"""
    engine = create_db_engine(get_settings())
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        missing = _REQUIRED_TABLES - tables
        if missing:
            raise RuntimeError(f"P1-004 downgrade removed required tables: {sorted(missing)}")
        if "workflow_definitions" in tables:
            raise RuntimeError("workflow_definitions still exists after P1-004 downgrade")
        print("P1-004 downgrade preserved all P1-001/P1-003 tables.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
