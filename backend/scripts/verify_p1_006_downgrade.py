"""验证 P1-006 downgrade 只移除自身字段并保留 P1-005 数据库结构。"""

import asyncio

from sqlalchemy import inspect

from app.core.config import get_settings
from app.infrastructure.database import create_db_engine


async def _verify() -> None:
    """检查 downgrade 后审批、工作流及主数据表仍存在且 P1-006 列已移除。"""
    engine = create_db_engine(get_settings())
    try:
        async with engine.connect() as connection:
            table_names = await connection.run_sync(
                lambda sync: set(inspect(sync).get_table_names())
            )
            expected_tables = {
                "approval_application",
                "approval_record",
                "master_data_change",
                "master_data_entities",
                "workflow_definitions",
                "users",
                "user_role_assignments",
                "auth_sessions",
            }
            missing_tables = expected_tables - table_names
            if missing_tables:
                raise RuntimeError(f"P1-005 downgrade 缺少既有表：{sorted(missing_tables)}")
            columns = await connection.run_sync(
                lambda sync: {
                    column["name"] for column in inspect(sync).get_columns("approval_application")
                }
            )
            if {"workflow_code", "workflow_version"} & columns:
                raise RuntimeError("P1-006 downgrade 后仍存在 workflow binding 字段")
            change_columns = await connection.run_sync(
                lambda sync: {
                    column["name"] for column in inspect(sync).get_columns("master_data_change")
                }
            )
            if not {"entity_key", "before_data", "base_entity_version"} <= change_columns:
                raise RuntimeError("P1-005 MasterDataChange 字段未被保留")
    finally:
        await engine.dispose()


def main() -> None:
    """运行异步 PostgreSQL downgrade schema 检查。"""
    asyncio.run(_verify())
    print("P1-006 downgrade preserved P1-005 schema")


if __name__ == "__main__":
    main()
