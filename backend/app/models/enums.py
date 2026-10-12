"""兼容旧 ORM 导入路径，枚举的唯一定义位于审批领域模块。"""

from app.domain.approval.enums import ApprovalAction, ApprovalStatus, BusinessType

__all__ = ["ApprovalAction", "ApprovalStatus", "BusinessType"]
