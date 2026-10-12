from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database import Base
from app.models.enums import ApprovalAction, ApprovalStatus, BusinessType

if TYPE_CHECKING:
    from app.models.master_data import MasterDataChange


class ApprovalApplication(Base):
    """保存审批申请的业务引用、当前状态和工作流上下文。"""

    __tablename__ = "approval_application"
    __table_args__ = (
        Index("ix_approval_application_status", "status"),
        Index("ix_approval_application_applicant_id", "applicant_id"),
        Index("ix_approval_application_business_type", "business_type"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    business_type: Mapped[BusinessType] = mapped_column(
        Enum(
            BusinessType,
            name="ck_approval_application_business_type",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [item.value for item in enum_class],
        ),
        nullable=False,
    )
    business_id: Mapped[str] = mapped_column(String(128), nullable=False)
    applicant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(
            ApprovalStatus,
            name="ck_approval_application_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [item.value for item in enum_class],
        ),
        default=ApprovalStatus.DRAFT,
        server_default=text("'DRAFT'"),
        nullable=False,
    )
    workflow_instance_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_node_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    current_node_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        nullable=False,
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    records: Mapped[list[ApprovalRecord]] = relationship(back_populates="approval")
    master_data_change: Mapped[MasterDataChange | None] = relationship(
        back_populates="approval", uselist=False
    )


class ApprovalRecord(Base):
    """保存申请的动作历史、状态变化和工作流节点快照。"""

    __tablename__ = "approval_record"
    __table_args__ = (Index("ix_approval_record_approval_id", "approval_id"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    approval_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_application.id", name="fk_approval_record_approval_id"),
        nullable=False,
    )
    operator_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[ApprovalAction] = mapped_column(
        Enum(
            ApprovalAction,
            name="ck_approval_record_action",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [item.value for item in enum_class],
        ),
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    from_status: Mapped[ApprovalStatus | None] = mapped_column(
        Enum(
            ApprovalStatus,
            name="ck_approval_record_from_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [item.value for item in enum_class],
        ),
        nullable=True,
    )
    to_status: Mapped[ApprovalStatus] = mapped_column(
        Enum(
            ApprovalStatus,
            name="ck_approval_record_to_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum_class: [item.value for item in enum_class],
        ),
        nullable=False,
    )
    from_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    from_node_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    from_node_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_node_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    target_node_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_operator_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    approval: Mapped[ApprovalApplication] = relationship(back_populates="records")
