"""定义审批领域共用的状态、动作和业务类型。"""

from enum import StrEnum


class ApprovalStatus(StrEnum):
    """表示审批申请当前所处阶段，不表示已经发生的审批动作。"""

    DRAFT = "DRAFT"
    IN_APPROVAL = "IN_APPROVAL"
    COMPLETED = "COMPLETED"
    VOIDED = "VOIDED"


class ApprovalAction(StrEnum):
    """记录审批过程中发生的动作，包括驳回和撤回等历史事件。"""

    CREATE = "CREATE"
    SAVE = "SAVE"
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    WITHDRAW = "WITHDRAW"
    VOID = "VOID"


class BusinessType(StrEnum):
    """标识审批申请关联的业务类型。"""

    MASTER_DATA_CHANGE = "MASTER_DATA_CHANGE"
