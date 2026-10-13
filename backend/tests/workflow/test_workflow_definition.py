"""工作流定义纯领域行为测试。"""

import pytest

from app.domain.auth.enums import UserRole
from app.domain.workflow.definition import (
    WorkflowDefinitionSpec,
    WorkflowNodeDefinition,
    parse_workflow_definition,
    workflow_definition_to_dict,
)
from app.domain.workflow.errors import WorkflowDefinitionError, WorkflowDefinitionErrorCode


def spec_payload() -> dict[str, object]:
    """构造标准两节点供应商变更流程 payload。"""
    return {
        "code": "supplier_change",
        "version": 1,
        "nodes": [
            {"key": "manager_review", "role": "APPROVER"},
            {"key": "data_admin_review", "role": "DATA_ADMIN"},
        ],
    }


def test_valid_workflow_parses_and_serializes_canonically() -> None:
    """验证合法节点顺序和 role value 可稳定往返。"""
    spec = parse_workflow_definition(spec_payload())

    assert spec == WorkflowDefinitionSpec(
        code="supplier_change",
        version=1,
        nodes=(
            WorkflowNodeDefinition("manager_review", UserRole.APPROVER),
            WorkflowNodeDefinition("data_admin_review", UserRole.DATA_ADMIN),
        ),
    )
    assert workflow_definition_to_dict(spec) == spec_payload()
    assert parse_workflow_definition(workflow_definition_to_dict(spec)) == spec


@pytest.mark.parametrize("code", ["", "Supplier_Change", "1workflow", "bad-name", "x" * 129])
def test_invalid_code_is_rejected(code: str) -> None:
    """验证非 lowercase snake_case code 被拒绝。"""
    payload = spec_payload()
    payload["code"] = code

    with pytest.raises(WorkflowDefinitionError) as error:
        parse_workflow_definition(payload)

    assert error.value.code is WorkflowDefinitionErrorCode.INVALID_CODE


@pytest.mark.parametrize("version", [0, -1, True, False, 1.5, "1"])
def test_invalid_version_is_rejected(version: object) -> None:
    """验证版本必须是正整数且 bool 不得冒充 int。"""
    payload = spec_payload()
    payload["version"] = version

    with pytest.raises(WorkflowDefinitionError) as error:
        parse_workflow_definition(payload)

    assert error.value.code is WorkflowDefinitionErrorCode.INVALID_VERSION


@pytest.mark.parametrize(
    ("nodes", "expected_code"),
    [
        ([], WorkflowDefinitionErrorCode.NODES_EMPTY),
        ([{"key": "Bad-Key", "role": "APPROVER"}], WorkflowDefinitionErrorCode.NODE_INVALID_KEY),
        (
            [
                {"key": "same_node", "role": "APPROVER"},
                {"key": "same_node", "role": "DATA_ADMIN"},
            ],
            WorkflowDefinitionErrorCode.NODE_DUPLICATE_KEY,
        ),
        (
            [{"key": "manager_review", "role": "UNKNOWN"}],
            WorkflowDefinitionErrorCode.NODE_INVALID_ROLE,
        ),
        (
            [{"key": "applicant_review", "role": "APPLICANT"}],
            WorkflowDefinitionErrorCode.NODE_ROLE_NOT_ALLOWED,
        ),
        (
            [{"key": "admin_review", "role": "ADMIN"}],
            WorkflowDefinitionErrorCode.NODE_ROLE_NOT_ALLOWED,
        ),
    ],
)
def test_invalid_nodes_are_rejected(
    nodes: object, expected_code: WorkflowDefinitionErrorCode
) -> None:
    """验证节点空值、key 冲突及不允许角色使用稳定错误码拒绝。"""
    payload = spec_payload()
    payload["nodes"] = nodes

    with pytest.raises(WorkflowDefinitionError) as error:
        parse_workflow_definition(payload)

    assert error.value.code is expected_code


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 1, "nodes": []},
        {**spec_payload(), "unexpected": True},
        {
            "code": "supplier_change",
            "version": 1,
            "nodes": [{"key": "x", "role": "APPROVER", "name": "x"}],
        },
        {"code": "supplier_change", "version": 1, "nodes": {}},
        None,
        [],
    ],
)
def test_invalid_payload_shape_is_rejected(payload: object) -> None:
    """验证缺字段、额外字段和非对象 payload 均按 strict contract 拒绝。"""
    with pytest.raises(WorkflowDefinitionError) as error:
        parse_workflow_definition(payload)

    assert error.value.code is WorkflowDefinitionErrorCode.PAYLOAD_INVALID


def test_node_navigation_handles_boundaries_and_unknown_keys() -> None:
    """验证首尾、中间节点导航及未知 key 的稳定错误。"""
    spec = parse_workflow_definition(spec_payload())

    assert spec.first_node().key == "manager_review"
    assert spec.final_node().key == "data_admin_review"
    assert spec.get_node("manager_review") is spec.nodes[0]
    assert spec.previous_node("manager_review") is None
    assert spec.next_node("manager_review") is spec.nodes[1]
    assert spec.previous_node("data_admin_review") is spec.nodes[0]
    assert spec.next_node("data_admin_review") is None
    assert spec.is_first_node("manager_review")
    assert not spec.is_final_node("manager_review")
    assert spec.is_final_node("data_admin_review")
    assert not spec.is_first_node("data_admin_review")
    with pytest.raises(WorkflowDefinitionError) as error:
        spec.get_node("missing_node")
    assert error.value.code is WorkflowDefinitionErrorCode.NODE_NOT_FOUND


def test_single_node_workflow_is_both_first_and_final() -> None:
    """验证单节点流程首尾标志同时成立且没有前后节点。"""
    spec = WorkflowDefinitionSpec(
        code="single_review",
        version=1,
        nodes=(WorkflowNodeDefinition("single_review", UserRole.APPROVER),),
    )

    assert spec.first_node() is spec.final_node()
    assert spec.is_first_node("single_review")
    assert spec.is_final_node("single_review")
    assert spec.previous_node("single_review") is None
    assert spec.next_node("single_review") is None


def test_active_is_persistence_state_not_part_of_workflow_spec() -> None:
    """验证不可变领域定义只包含 code、version 和有序 nodes。"""
    assert set(WorkflowDefinitionSpec.__dataclass_fields__) == {"code", "version", "nodes"}


def test_workflow_domain_has_no_infrastructure_dependencies() -> None:
    """验证纯工作流领域模块不导入 ORM、Web 或 infrastructure。"""
    from pathlib import Path

    domain_path = Path(__file__).parents[2] / "app" / "domain" / "workflow"
    source = "\n".join(path.read_text(encoding="utf-8") for path in domain_path.glob("*.py"))

    for forbidden in ("fastapi", "sqlalchemy", "app.models", "app.infrastructure"):
        assert forbidden not in source.lower()
