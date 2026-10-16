"""确认 P1-010 downgrade 只移除本任务新增的两张表。"""

import asyncio

from sqlalchemy import inspect

from app.core.config import get_settings
from app.infrastructure.database import create_db_engine

_P1_009_TABLES = {
    "approval_application",
    "approval_record",
    "master_data_change",
    "master_data_entities",
    "users",
    "user_roles",
    "auth_sessions",
    "workflow_definitions",
}


async def _verify() -> None:
    """检查 downgrade 后 P1-009 表仍在且幂等审计表已移除。"""
    engine = create_db_engine(get_settings())
    try:
        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        missing = _P1_009_TABLES - tables
        if missing:
            raise RuntimeError(f"P1-010 downgrade removed P1-009 tables: {sorted(missing)}")
        leftover = {"idempotency_records", "audit_logs"} & tables
        if leftover:
            raise RuntimeError(f"P1-010 tables remain after downgrade: {sorted(leftover)}")
    finally:
        await engine.dispose()


def main() -> None:
    """运行 P1-010 downgrade 的 PostgreSQL schema 验证。"""
    asyncio.run(_verify())
    print("P1-010 downgrade preserved P1-009 schema.")


if __name__ == "__main__":
    main()
