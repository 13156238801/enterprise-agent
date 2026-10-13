"""定义流程定义校验与导航的稳定领域错误。"""

from enum import StrEnum


class WorkflowDefinitionErrorCode(StrEnum):
    """列举调用方可稳定识别的工作流定义错误代码。"""

    INVALID_CODE = "WORKFLOW_DEFINITION_INVALID_CODE"
    INVALID_VERSION = "WORKFLOW_DEFINITION_INVALID_VERSION"
    NODES_EMPTY = "WORKFLOW_DEFINITION_NODES_EMPTY"
    NODE_INVALID_KEY = "WORKFLOW_NODE_INVALID_KEY"
    NODE_DUPLICATE_KEY = "WORKFLOW_NODE_DUPLICATE_KEY"
    NODE_INVALID_ROLE = "WORKFLOW_NODE_INVALID_ROLE"
    NODE_ROLE_NOT_ALLOWED = "WORKFLOW_NODE_ROLE_NOT_ALLOWED"
    NODE_NOT_FOUND = "WORKFLOW_NODE_NOT_FOUND"
    PAYLOAD_INVALID = "WORKFLOW_DEFINITION_PAYLOAD_INVALID"


class WorkflowDefinitionError(Exception):
    """携带稳定错误码和内部诊断说明的流程定义领域异常。"""

    def __init__(self, code: WorkflowDefinitionErrorCode, message: str) -> None:
        """保存机器可读错误码与面向开发者的原因。"""
        self.code = code
        super().__init__(message)
