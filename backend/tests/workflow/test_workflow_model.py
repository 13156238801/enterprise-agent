"""工作流 ORM metadata 契约测试。"""

import pytest
from sqlalchemy import CheckConstraint, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.domain.workflow.errors import WorkflowDefinitionError, WorkflowDefinitionErrorCode
from app.models.workflow import WorkflowDefinition
from app.workflow.repository import WorkflowDefinitionRepository


def test_workflow_definition_metadata_has_version_constraints_and_active_index() -> None:
    """验证工作流表字段、版本约束、唯一键和 active partial index。"""
    table = WorkflowDefinition.__table__

    assert table.c.id.primary_key
    assert table.c.code.type.length == 128
    assert table.c.version.type.python_type is int
    assert isinstance(table.c.definition_json.type, JSONB)
    assert table.c.definition_json.nullable is False
    assert table.c.active.nullable is False
    assert any(
        isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_workflow_definitions_version_positive"
        and "version > 0" in str(constraint.sqltext)
        for constraint in table.constraints
    )
    assert any(
        isinstance(constraint, UniqueConstraint)
        and constraint.name == "uq_workflow_definitions_code_version"
        and {column.name for column in constraint.columns} == {"code", "version"}
        for constraint in table.constraints
    )
    active_index = next(
        index for index in table.indexes if index.name == "uq_workflow_definitions_active_code"
    )
    assert isinstance(active_index, Index)
    assert active_index.unique
    assert str(active_index.dialect_options["postgresql"]["where"]) == "active = true"
    assert table.c.created_at.type.timezone
    assert table.c.updated_at.type.timezone


def test_repository_rejects_row_and_json_metadata_mismatch() -> None:
    """验证数据库索引列和 definition_json 不一致时按 payload 错误拒绝。"""
    row = WorkflowDefinition(
        code="supplier_change",
        version=1,
        definition_json={
            "code": "different_workflow",
            "version": 1,
            "nodes": [{"key": "manager_review", "role": "APPROVER"}],
        },
        active=False,
    )

    with pytest.raises(WorkflowDefinitionError) as error:
        WorkflowDefinitionRepository._to_spec(row)

    assert error.value.code is WorkflowDefinitionErrorCode.PAYLOAD_INVALID
