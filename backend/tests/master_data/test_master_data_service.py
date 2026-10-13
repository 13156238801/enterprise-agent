"""主数据草稿 Service 的 PostgreSQL 持久化与事务测试。"""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from app.auth.context import AuthContext
from app.core.config import Settings, get_settings
from app.domain.approval.enums import ApprovalAction, ApprovalStatus, BusinessType
from app.domain.auth.enums import UserRole
from app.infrastructure.database import create_db_engine, create_session_factory
from app.master_data.errors import MasterDataError, MasterDataErrorCode
from app.master_data.repository import MasterDataRepository
from app.master_data.schemas import ChangeDraftRequest
from app.master_data.service import MasterDataService
from app.models.approval import ApprovalApplication, ApprovalRecord
from app.models.master_data import MasterDataChange, MasterDataEntity


def _auth_context(user_id: UUID | None = None) -> AuthContext:
    """构造集成测试使用的 APPLICANT 服务端身份。"""
    return AuthContext(
        session_id=uuid4(),
        user_id=user_id or uuid4(),
        username="master-data-test",
        display_name="Master Data Test",
        roles=frozenset({UserRole.APPLICANT}),
    )


def test_postgres_create_and_iterative_update_preserve_original_snapshots() -> None:
    """验证草稿创建、迭代保存、完整 Diff 和当前实体不变。"""

    async def run() -> None:
        """在随机实体上执行 create/update 并清理所有测试行。"""
        engine = create_db_engine(get_settings())
        factory = create_session_factory(engine)
        entity_key = f"supplier-{uuid4().hex}"
        auth = _auth_context()
        approval_ids: list[UUID] = []
        try:
            async with factory() as session:
                entity = MasterDataEntity(
                    entity_type="supplier",
                    entity_key=entity_key,
                    data_json={"currency": "CNY", "payment_terms": "PREPAID"},
                    version=1,
                )
                session.add(entity)
                await session.commit()
                entity_id = entity.id

            async with factory() as session:
                first = await MasterDataService(session).create_or_update_draft(
                    "supplier",
                    entity_key,
                    ChangeDraftRequest(
                        changes={"payment_terms": "NET_30"},
                        reason="  更新账期  ",
                    ),
                    auth,
                )
                approval_ids.append(first.approval_id)

            assert first.status is ApprovalStatus.DRAFT
            assert first.before == {"currency": "CNY", "payment_terms": "PREPAID"}
            assert first.after == {"currency": "CNY", "payment_terms": "NET_30"}
            assert first.base_entity_version == 1
            assert first.reason == "更新账期"
            assert [(item.field, item.operation.value) for item in first.diff] == [
                ("payment_terms", "UPDATE")
            ]

            async with factory() as session:
                entity = await session.get(MasterDataEntity, entity_id)
                assert entity is not None
                assert entity.version == 1
                assert entity.data_json == {
                    "currency": "CNY",
                    "payment_terms": "PREPAID",
                }

            async with factory() as session:
                entity = await session.get(MasterDataEntity, entity_id)
                assert entity is not None
                entity.data_json = {"currency": "CNY", "payment_terms": "CHANGED_EXTERNALLY"}
                entity.version = 2
                await session.commit()

            async with factory() as session:
                second = await MasterDataService(session).create_or_update_draft(
                    "supplier",
                    entity_key,
                    ChangeDraftRequest(
                        draft_approval_id=first.approval_id,
                        changes={"currency": "USD"},
                        reason="更新币种",
                    ),
                    auth,
                )
            assert second.before == first.before
            assert second.base_entity_version == 1
            assert second.after == {"currency": "USD", "payment_terms": "NET_30"}
            assert {item.field for item in second.diff} == {"currency", "payment_terms"}

            async with factory() as session:
                third = await MasterDataService(session).create_or_update_draft(
                    "supplier",
                    entity_key,
                    ChangeDraftRequest(
                        draft_approval_id=first.approval_id,
                        changes={"payment_terms": "PREPAID"},
                        reason="撤销账期差异",
                    ),
                    auth,
                )
            assert third.before == first.before
            assert third.base_entity_version == first.base_entity_version
            assert [(item.field, item.operation.value) for item in third.diff] == [
                ("currency", "UPDATE")
            ]

            async with factory() as session:
                application = await session.get(ApprovalApplication, first.approval_id)
                change = await session.get(MasterDataChange, first.change_id)
                records = list(
                    (
                        await session.scalars(
                            select(ApprovalRecord)
                            .where(ApprovalRecord.approval_id == first.approval_id)
                            .order_by(ApprovalRecord.created_at, ApprovalRecord.id)
                        )
                    ).all()
                )
                entity = await session.get(MasterDataEntity, entity_id)
                assert application is not None and change is not None and entity is not None
                assert application.business_type is BusinessType.MASTER_DATA_CHANGE
                assert application.business_id == str(change.id)
                assert change.approval_id == application.id
                assert change.before_data == first.before
                assert change.base_entity_version == 1
                assert [record.action for record in records] == [
                    ApprovalAction.CREATE,
                    ApprovalAction.SAVE,
                    ApprovalAction.SAVE,
                ]
                assert records[0].from_status is None
                assert records[0].to_status is ApprovalStatus.DRAFT
                assert records[1].from_status is ApprovalStatus.DRAFT
                assert records[1].to_status is ApprovalStatus.DRAFT
                assert entity.version == 2
                assert entity.data_json == {
                    "currency": "CNY",
                    "payment_terms": "CHANGED_EXTERNALLY",
                }

            async with factory() as session:
                with pytest.raises(MasterDataError) as error:
                    await MasterDataService(session).create_or_update_draft(
                        "supplier",
                        entity_key,
                        ChangeDraftRequest(
                            draft_approval_id=first.approval_id,
                            changes={"currency": "USD"},
                            reason="不应写入",
                        ),
                        auth,
                    )
                assert error.value.code == MasterDataErrorCode.NO_CHANGE.value

            async with factory() as session:
                count = await session.scalar(
                    select(func.count())
                    .select_from(ApprovalRecord)
                    .where(ApprovalRecord.approval_id == first.approval_id)
                )
                assert count == 3

            async with factory() as session:
                with pytest.raises(MasterDataError) as error:
                    await MasterDataService(session).create_or_update_draft(
                        "supplier",
                        entity_key,
                        ChangeDraftRequest(
                            draft_approval_id=first.approval_id,
                            changes={"name": "unauthorized"},
                            reason="非申请人",
                        ),
                        _auth_context(),
                    )
                assert error.value.code == MasterDataErrorCode.DRAFT_FORBIDDEN.value

            async with factory() as session:
                with pytest.raises(MasterDataError) as error:
                    await MasterDataService(session).create_or_update_draft(
                        "customer",
                        entity_key,
                        ChangeDraftRequest(
                            draft_approval_id=first.approval_id,
                            changes={"name": "mismatch"},
                            reason="身份不匹配",
                        ),
                        auth,
                    )
                assert error.value.code == MasterDataErrorCode.DRAFT_ENTITY_MISMATCH.value

            async with factory() as session:
                approval = await session.get(ApprovalApplication, first.approval_id)
                assert approval is not None
                approval.status = ApprovalStatus.IN_APPROVAL
                await session.commit()
            async with factory() as session:
                with pytest.raises(MasterDataError) as error:
                    await MasterDataService(session).create_or_update_draft(
                        "supplier",
                        entity_key,
                        ChangeDraftRequest(
                            draft_approval_id=first.approval_id,
                            changes={"name": "not-editable"},
                            reason="不可编辑状态",
                        ),
                        auth,
                    )
                assert error.value.code == MasterDataErrorCode.DRAFT_NOT_EDITABLE.value
        finally:
            async with factory() as session:
                if approval_ids:
                    await session.execute(
                        delete(MasterDataChange).where(
                            MasterDataChange.approval_id.in_(approval_ids)
                        )
                    )
                    await session.execute(
                        delete(ApprovalRecord).where(ApprovalRecord.approval_id.in_(approval_ids))
                    )
                    await session.execute(
                        delete(ApprovalApplication).where(ApprovalApplication.id.in_(approval_ids))
                    )
                await session.execute(
                    delete(MasterDataEntity).where(
                        MasterDataEntity.entity_type == "supplier",
                        MasterDataEntity.entity_key == entity_key,
                    )
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(run())


def test_postgres_create_draft_rolls_back_after_flush(monkeypatch) -> None:
    """验证 CREATE flush 后异常会回滚 Approval、Change 和 CREATE record。"""

    async def run() -> None:
        """注入写入后故障，并用独立 Session 检查事务无残留。"""
        engine = create_db_engine(Settings())
        factory = create_session_factory(engine)
        entity_key = f"rollback-{uuid4().hex}"
        auth = _auth_context()
        observed_approval_ids: list[UUID] = []
        async with factory() as session:
            session.add(
                MasterDataEntity(
                    entity_type="supplier",
                    entity_key=entity_key,
                    data_json={"enabled": True},
                    version=1,
                )
            )
            await session.commit()
        original_add = MasterDataRepository.add_draft

        async def flush_then_fail(repository, approval, change, record) -> None:
            """先实际 flush 三条关联记录，再注入事务异常。"""
            observed_approval_ids.append(approval.id)
            await original_add(repository, approval, change, record)
            raise RuntimeError("injected create failure")

        monkeypatch.setattr(MasterDataRepository, "add_draft", flush_then_fail)
        try:
            async with factory() as session:
                with pytest.raises(RuntimeError, match="injected create failure"):
                    await MasterDataService(session).create_or_update_draft(
                        "supplier",
                        entity_key,
                        ChangeDraftRequest(changes={"enabled": False}, reason="test"),
                        auth,
                    )
            async with factory() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ApprovalApplication)
                        .where(ApprovalApplication.id == observed_approval_ids[0])
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(MasterDataChange)
                        .where(MasterDataChange.approval_id == observed_approval_ids[0])
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ApprovalRecord)
                        .where(ApprovalRecord.approval_id == observed_approval_ids[0])
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(MasterDataEntity)
                        .where(MasterDataEntity.entity_key == entity_key)
                    )
                    == 1
                )
        finally:
            async with factory() as session:
                await session.execute(
                    delete(MasterDataEntity).where(MasterDataEntity.entity_key == entity_key)
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(run())


def test_postgres_update_draft_rolls_back_after_flush(monkeypatch) -> None:
    """验证 SAVE flush 后异常恢复原草稿且不留下 SAVE 历史。"""

    async def run() -> None:
        """创建 draft 后注入 SAVE 故障并查询数据库回滚结果。"""
        engine = create_db_engine(Settings())
        factory = create_session_factory(engine)
        entity_key = f"update-rollback-{uuid4().hex}"
        auth = _auth_context()
        approval_id = None
        try:
            async with factory() as session:
                session.add(
                    MasterDataEntity(
                        entity_type="supplier",
                        entity_key=entity_key,
                        data_json={"payment_terms": "PREPAID"},
                        version=1,
                    )
                )
                await session.commit()
            async with factory() as session:
                draft = await MasterDataService(session).create_or_update_draft(
                    "supplier",
                    entity_key,
                    ChangeDraftRequest(changes={"payment_terms": "NET_30"}, reason="create"),
                    auth,
                )
                approval_id = draft.approval_id

            original_save = MasterDataRepository.save_draft

            async def flush_then_fail(repository, change, record) -> None:
                """先 flush 更新快照和 SAVE record，再模拟服务异常。"""
                await original_save(repository, change, record)
                raise RuntimeError("injected update failure")

            monkeypatch.setattr(MasterDataRepository, "save_draft", flush_then_fail)
            async with factory() as session:
                with pytest.raises(RuntimeError, match="injected update failure"):
                    await MasterDataService(session).create_or_update_draft(
                        "supplier",
                        entity_key,
                        ChangeDraftRequest(
                            draft_approval_id=approval_id,
                            changes={"currency": "USD"},
                            reason="update",
                        ),
                        auth,
                    )
            async with factory() as session:
                change = await session.get(MasterDataChange, draft.change_id)
                records = list(
                    (
                        await session.scalars(
                            select(ApprovalRecord).where(ApprovalRecord.approval_id == approval_id)
                        )
                    ).all()
                )
                assert change is not None
                assert change.form_data == draft.after
                assert change.change_reason == "create"
                assert len(records) == 1
        finally:
            async with factory() as session:
                if approval_id is not None:
                    await session.execute(
                        delete(MasterDataChange).where(MasterDataChange.approval_id == approval_id)
                    )
                    await session.execute(
                        delete(ApprovalRecord).where(ApprovalRecord.approval_id == approval_id)
                    )
                    await session.execute(
                        delete(ApprovalApplication).where(ApprovalApplication.id == approval_id)
                    )
                await session.execute(
                    delete(MasterDataEntity).where(MasterDataEntity.entity_key == entity_key)
                )
                await session.commit()
            await engine.dispose()

    asyncio.run(run())
