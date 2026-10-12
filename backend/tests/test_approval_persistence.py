import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.infrastructure.database import create_db_engine, create_session_factory
from app.models import (
    ApprovalAction,
    ApprovalApplication,
    ApprovalRecord,
    ApprovalStatus,
    BusinessType,
    MasterDataChange,
)


def test_postgres_persists_approval_models_and_relationships() -> None:
    """在真实 PostgreSQL 提交并重新查询三类对象，验证关系、默认值和外键约束。"""
    engine = create_db_engine(get_settings())
    session_factory = create_session_factory(engine)
    approval_id = uuid4()

    async def run_integration_test() -> None:
        """执行持久化断言并在 finally 中显式清理本测试创建的数据。"""
        try:
            try:
                async with session_factory() as session:
                    application = ApprovalApplication(
                        id=approval_id,
                        business_type=BusinessType.MASTER_DATA_CHANGE,
                        business_id=f"demo-business-{approval_id}",
                        applicant_id="demo-applicant-001",
                    )
                    record = ApprovalRecord(
                        approval=application,
                        operator_id="demo-applicant-001",
                        action=ApprovalAction.CREATE,
                        from_status=None,
                        to_status=ApprovalStatus.DRAFT,
                    )
                    change = MasterDataChange(
                        approval=application,
                        entity_type="demo_asset",
                        entity_id="external-asset-001",
                        form_data={"name": "Demo Asset"},
                        change_items=[
                            {
                                "field": "name",
                                "field_label": "名称",
                                "before": "Old Demo Name",
                                "after": "New Demo Name",
                            }
                        ],
                    )
                    session.add_all([application, record, change])
                    await session.commit()
                    record_id = record.id
                    change_id = change.id

                async with session_factory() as session:
                    result = await session.execute(
                        select(ApprovalApplication)
                        .options(
                            selectinload(ApprovalApplication.records),
                            selectinload(ApprovalApplication.master_data_change),
                        )
                        .where(ApprovalApplication.id == approval_id)
                    )
                    loaded = result.scalar_one()
                    assert loaded.status is ApprovalStatus.DRAFT
                    assert loaded.version == 1
                    assert loaded.created_at.tzinfo is not None
                    assert loaded.updated_at.tzinfo is not None
                    assert len(loaded.records) == 1
                    assert loaded.records[0].id == record_id
                    assert loaded.records[0].approval_id == approval_id
                    assert loaded.master_data_change.id == change_id
                    assert loaded.master_data_change.ext_data is None
                    assert loaded.master_data_change.form_data == {"name": "Demo Asset"}
                    assert loaded.master_data_change.change_items[0]["after"] == "New Demo Name"

                async with session_factory() as session:
                    duplicate_change = MasterDataChange(
                        approval_id=approval_id,
                        entity_type="demo_asset_duplicate",
                        entity_id="external-asset-duplicate",
                        form_data={},
                        change_items=[],
                    )
                    session.add(duplicate_change)
                    with pytest.raises(IntegrityError):
                        await session.commit()
                    await session.rollback()

                async with session_factory() as session:
                    with pytest.raises(IntegrityError):
                        await session.execute(
                            text(
                                "UPDATE approval_application "
                                "SET status = 'INVALIDSTAT' WHERE id = :approval_id"
                            ),
                            {"approval_id": approval_id},
                        )
                        await session.commit()
                    await session.rollback()

                async with session_factory() as session:
                    session.add(
                        ApprovalRecord(
                            approval_id=uuid4(),
                            operator_id="demo-operator-invalid-fk",
                            action=ApprovalAction.SAVE,
                            to_status=ApprovalStatus.DRAFT,
                        )
                    )
                    with pytest.raises(IntegrityError):
                        await session.commit()
                    await session.rollback()
            finally:
                async with session_factory() as session:
                    await session.execute(
                        delete(MasterDataChange).where(MasterDataChange.approval_id == approval_id)
                    )
                    await session.execute(
                        delete(ApprovalRecord).where(ApprovalRecord.approval_id == approval_id)
                    )
                    await session.execute(
                        delete(ApprovalApplication).where(ApprovalApplication.id == approval_id)
                    )
                    await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(run_integration_test())
