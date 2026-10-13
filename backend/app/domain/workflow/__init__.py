"""导出不可变工作流定义领域类型与 JSON codec。"""

from app.domain.workflow.definition import (
    WorkflowDefinitionSpec,
    WorkflowNodeDefinition,
    parse_workflow_definition,
    workflow_definition_to_dict,
)
from app.domain.workflow.errors import WorkflowDefinitionError, WorkflowDefinitionErrorCode

__all__ = [
    "WorkflowDefinitionError",
    "WorkflowDefinitionErrorCode",
    "WorkflowDefinitionSpec",
    "WorkflowNodeDefinition",
    "parse_workflow_definition",
    "workflow_definition_to_dict",
]
