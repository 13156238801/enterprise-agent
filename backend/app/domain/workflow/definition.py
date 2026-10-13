"""定义不可变工作流模板、严格 JSON codec 与节点导航规则。"""

import re
from dataclasses import dataclass
from typing import Any

from app.domain.auth.enums import UserRole
from app.domain.workflow.errors import WorkflowDefinitionError, WorkflowDefinitionErrorCode

_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_ALLOWED_NODE_ROLES = frozenset({UserRole.APPROVER, UserRole.DATA_ADMIN})
_DEFINITION_FIELDS = frozenset({"code", "version", "nodes"})
_NODE_FIELDS = frozenset({"key", "role"})


def _validate_identifier(value: object, *, node_key: bool = False) -> None:
    """校验机器标识格式并使用对应领域错误码报告非法值。"""
    error_code = (
        WorkflowDefinitionErrorCode.NODE_INVALID_KEY
        if node_key
        else WorkflowDefinitionErrorCode.INVALID_CODE
    )
    if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
        label = "节点 key" if node_key else "流程 code"
        raise WorkflowDefinitionError(error_code, f"{label} 必须为 lowercase snake_case。")


@dataclass(frozen=True)
class WorkflowNodeDefinition:
    """表示只声明稳定节点键和所需角色的不可变审批节点，不绑定具体处理人。"""

    key: str
    role: UserRole

    def __post_init__(self) -> None:
        """确保直接构造节点时也不能绕过 key 与角色边界校验。"""
        _validate_identifier(self.key, node_key=True)
        if not isinstance(self.role, UserRole):
            raise WorkflowDefinitionError(
                WorkflowDefinitionErrorCode.NODE_INVALID_ROLE, "节点 role 必须是已定义角色。"
            )
        if self.role not in _ALLOWED_NODE_ROLES:
            raise WorkflowDefinitionError(
                WorkflowDefinitionErrorCode.NODE_ROLE_NOT_ALLOWED,
                "审批节点仅允许 APPROVER 或 DATA_ADMIN。",
            )


@dataclass(frozen=True)
class WorkflowDefinitionSpec:
    """表示 code/version/nodes 不可变且经验证的流程快照，修改流程必须创建新版本。"""

    code: str
    version: int
    nodes: tuple[WorkflowNodeDefinition, ...]

    def __post_init__(self) -> None:
        """验证直接构造的定义并拒绝可变或重复节点集合。"""
        _validate_identifier(self.code)
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version <= 0:
            raise WorkflowDefinitionError(
                WorkflowDefinitionErrorCode.INVALID_VERSION, "流程版本必须是正整数。"
            )
        if not isinstance(self.nodes, tuple):
            if isinstance(self.nodes, list) and not self.nodes:
                raise WorkflowDefinitionError(
                    WorkflowDefinitionErrorCode.NODES_EMPTY, "流程至少需要一个审批节点。"
                )
            raise WorkflowDefinitionError(
                WorkflowDefinitionErrorCode.PAYLOAD_INVALID,
                "领域定义的 nodes 必须使用不可变 tuple。",
            )
        if not self.nodes:
            raise WorkflowDefinitionError(
                WorkflowDefinitionErrorCode.NODES_EMPTY, "流程至少需要一个审批节点。"
            )
        if any(not isinstance(node, WorkflowNodeDefinition) for node in self.nodes):
            raise WorkflowDefinitionError(
                WorkflowDefinitionErrorCode.PAYLOAD_INVALID, "nodes 必须只包含有效节点定义。"
            )
        keys = [node.key for node in self.nodes]
        if len(keys) != len(set(keys)):
            raise WorkflowDefinitionError(
                WorkflowDefinitionErrorCode.NODE_DUPLICATE_KEY,
                "同一流程版本不能包含重复 node key。",
            )

    def first_node(self) -> WorkflowNodeDefinition:
        """返回流程首节点。"""
        return self.nodes[0]

    def final_node(self) -> WorkflowNodeDefinition:
        """返回流程最终节点。"""
        return self.nodes[-1]

    def get_node(self, key: str) -> WorkflowNodeDefinition:
        """按稳定 key 查找节点，未知 key 抛出稳定领域错误。"""
        for node in self.nodes:
            if node.key == key:
                return node
        raise WorkflowDefinitionError(
            WorkflowDefinitionErrorCode.NODE_NOT_FOUND, f"流程中不存在节点 {key!r}。"
        )

    def next_node(self, key: str) -> WorkflowNodeDefinition | None:
        """返回指定节点的下一节点；最终节点返回 None。"""
        index = self._node_index(key)
        return self.nodes[index + 1] if index + 1 < len(self.nodes) else None

    def previous_node(self, key: str) -> WorkflowNodeDefinition | None:
        """返回指定节点的前一节点；首节点返回 None。"""
        index = self._node_index(key)
        return self.nodes[index - 1] if index > 0 else None

    def is_first_node(self, key: str) -> bool:
        """判断指定节点是否为首节点。"""
        return self._node_index(key) == 0

    def is_final_node(self, key: str) -> bool:
        """判断指定节点是否为最终节点。"""
        return self._node_index(key) == len(self.nodes) - 1

    def _node_index(self, key: str) -> int:
        """返回节点在定义顺序中的索引并拒绝未知 key。"""
        for index, node in enumerate(self.nodes):
            if node.key == key:
                return index
        raise WorkflowDefinitionError(
            WorkflowDefinitionErrorCode.NODE_NOT_FOUND, f"流程中不存在节点 {key!r}。"
        )


def parse_workflow_definition(payload: object) -> WorkflowDefinitionSpec:
    """严格解析 JSON 对象并构造经过完整校验的不可变定义。"""
    if not isinstance(payload, dict) or set(payload) != _DEFINITION_FIELDS:
        raise WorkflowDefinitionError(
            WorkflowDefinitionErrorCode.PAYLOAD_INVALID,
            "流程 payload 顶层字段必须且仅包含 code、version、nodes。",
        )

    code = payload["code"]
    version = payload["version"]
    raw_nodes = payload["nodes"]
    _validate_identifier(code)
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        raise WorkflowDefinitionError(
            WorkflowDefinitionErrorCode.INVALID_VERSION, "流程版本必须是正整数。"
        )
    if not isinstance(raw_nodes, list):
        raise WorkflowDefinitionError(
            WorkflowDefinitionErrorCode.PAYLOAD_INVALID, "nodes 必须是数组。"
        )
    if not raw_nodes:
        raise WorkflowDefinitionError(
            WorkflowDefinitionErrorCode.NODES_EMPTY, "流程至少需要一个审批节点。"
        )

    nodes: list[WorkflowNodeDefinition] = []
    for raw_node in raw_nodes:
        if not isinstance(raw_node, dict) or set(raw_node) != _NODE_FIELDS:
            raise WorkflowDefinitionError(
                WorkflowDefinitionErrorCode.PAYLOAD_INVALID,
                "每个 node 必须且仅包含 key、role。",
            )
        key = raw_node["key"]
        role_value = raw_node["role"]
        _validate_identifier(key, node_key=True)
        if not isinstance(role_value, str):
            raise WorkflowDefinitionError(
                WorkflowDefinitionErrorCode.NODE_INVALID_ROLE, "节点 role 必须是角色字符串。"
            )
        try:
            role = UserRole(role_value)
        except ValueError as error:
            raise WorkflowDefinitionError(
                WorkflowDefinitionErrorCode.NODE_INVALID_ROLE, "节点 role 不是已定义角色。"
            ) from error
        nodes.append(WorkflowNodeDefinition(key=key, role=role))

    return WorkflowDefinitionSpec(code=code, version=version, nodes=tuple(nodes))


def workflow_definition_to_dict(spec: WorkflowDefinitionSpec) -> dict[str, Any]:
    """将有效定义序列化为字段顺序稳定且仅含 JSON 值的字典。"""
    return {
        "code": spec.code,
        "version": spec.version,
        "nodes": [{"key": node.key, "role": node.role.value} for node in spec.nodes],
    }
