# TASK-P1-001 — Approval Domain 数据模型与首个 Alembic Migration（修订版）

## 1. 目标
基于真实企业主数据审批经验，建立第一版通用 Approval Domain 持久化模型。本 TASK 只做模型、枚举、约束、关系、Alembic migration 和测试，不实现 API、状态机、权限或 Agent。

## 2. 核心原则
- Status 表示“当前处于什么阶段”
- Action 表示“发生过什么动作”
- ApprovalRecord 保存历史动作与节点流转
- Workflow Context 保存当前流程实例、Task、Node
- WITHDRAW / REJECT 是动作，不是长期状态

## 3. ApprovalStatus
```text
DRAFT
IN_APPROVAL
COMPLETED
VOIDED
```

## 4. ApprovalAction
```text
CREATE
SAVE
SUBMIT
APPROVE
REJECT
WITHDRAW
VOID
```

## 5. BusinessType
```text
MASTER_DATA_CHANGE
```

## 6. ApprovalApplication
表：`approval_application`

字段：
```text
id
business_type
business_id
applicant_id
status
workflow_instance_id
current_task_id
current_node_code
current_node_name
version
submitted_at
completed_at
created_at
updated_at
```

要求：
- UUID 主键，应用侧生成
- `status` 默认 `DRAFT`
- `version` 默认 1
- workflow/task/node 在 DRAFT 阶段允许为空
- 所有时间字段使用 timezone-aware datetime
- `business_id` 不跨系统建 FK
- status 与 workflow context 必须分离

## 7. ApprovalRecord
表：`approval_record`

字段：
```text
id
approval_id
operator_id
action
comment
from_status
to_status
from_task_id
from_node_code
from_node_name
target_task_id
target_node_code
target_node_name
target_operator_id
created_at
```

要求：
- `approval_id` FK → `approval_application.id`
- 推荐 `ON DELETE CASCADE`
- `from_status` 可为空
- `to_status` 不可为空
- target 相关字段允许为空
- 不增加 `reject_count`
- 如需统计驳回次数，以 `COUNT(action = REJECT)` 为准

## 8. MasterDataChange
表：`master_data_change`

字段：
```text
id
approval_id
entity_type
entity_id
form_data
ext_data
change_items
change_reason
created_at
updated_at
```

要求：
- `approval_id` FK → `approval_application.id`
- `approval_id` UNIQUE，形成 1:1
- `form_data` / `ext_data` / `change_items` 使用 PostgreSQL JSONB
- `change_reason` nullable
- 不使用真实公司数据

`change_items` 推荐：
```json
[
  {
    "field": "name",
    "field_label": "设备名称",
    "before": "Old Demo Name",
    "after": "New Demo Name"
  }
]
```

## 9. Relationships
```text
ApprovalApplication
  ├── 1:N ApprovalRecord
  └── 1:1 MasterDataChange
```

建议 ORM relationship：
```text
ApprovalApplication.records
ApprovalApplication.master_data_change
ApprovalRecord.approval
MasterDataChange.approval
```

## 10. 约束与索引
ApprovalApplication：
- PK: id
- index: status
- index: applicant_id
- index: business_type

ApprovalRecord：
- PK: id
- FK: approval_id
- index: approval_id

MasterDataChange：
- PK: id
- FK: approval_id
- UNIQUE: approval_id

不要无依据增加索引。

## 11. 真实流程规则（本 TASK 仅保证模型可表达，不实现）
```text
DRAFT --SUBMIT--> IN_APPROVAL

IN_APPROVAL --APPROVE(中间节点)--> IN_APPROVAL
IN_APPROVAL --APPROVE(最终节点)--> COMPLETED

IN_APPROVAL --REJECT(提交人)--> DRAFT
IN_APPROVAL --REJECT(前序审批节点)--> IN_APPROVAL

IN_APPROVAL --WITHDRAW--> DRAFT
条件：
- 操作者是申请人
- 当前仍在第一个审批节点
- 已经经过前序审批节点后，不允许申请人撤回
```

## 12. Package Structure
```text
backend/app/models/
├── __init__.py
├── enums.py
├── approval.py
└── master_data.py
```

所有 ORM Model 继承统一 `Base`。

## 13. Alembic
创建首份业务 migration：
```text
create_approval_domain_tables
```

只创建：
```text
approval_application
approval_record
master_data_change
```

要求：
- upgrade 成功
- downgrade 成功
- FK / UNIQUE / INDEX 与 ORM metadata 一致
- 不写 seed 数据
- 不创建 User / Role / Agent / Tool / Contract 表

## 14. 注释规范
- 所有函数/方法必须有简洁中文 docstring
- 所有 ORM Model 必须有中文 docstring
- 所有 Enum 必须有中文 docstring
- 每个测试函数必须有中文 docstring
- 关键设计加中文注释，重点解释“为什么”

## 15. Required Tests
1. Enum Values
2. Metadata 只新增三张业务表
3. status 默认 DRAFT、version 默认 1
4. workflow/task/node 在 DRAFT 阶段允许为空
5. FK 正确
6. `master_data_change.approval_id` UNIQUE
7. JSONB 类型正确
8. 真实 PostgreSQL persistence：创建三类对象、commit、重新查询、验证 relationship/FK
9. `alembic upgrade head`
10. `alembic downgrade base` 后重新 `upgrade head`
11. 测试与 migration 中无真实公司数据

## 16. Out of Scope
禁止实现：
- Approval API
- submit / approve / reject / withdraw service
- State Machine
- Permission / Authentication
- User / Role
- Optimistic locking logic
- Idempotency
- Tool / Agent / HITL
- RAG / Contract / Router / MCP / Memory
- 前端

## 17. Definition of Done
- 只新增三张授权业务表
- Status / Action 分离
- 没有 REJECTED / WITHDRAWN 主状态
- Workflow Context 与 status 分离
- UUID / timezone-aware datetime 策略一致
- status 默认 DRAFT
- version 默认 1
- JSONB / FK / UNIQUE / relationship 正确
- Alembic upgrade/downgrade 成功
- 最终重新 upgrade head
- 真实 PostgreSQL persistence test 通过
- 无真实公司数据
- 每个函数/方法/Model/Enum/test 有中文说明
- Ruff / pytest / git diff --check 通过
- 未开始下一 TASK

## 18. Completion Report
Codex 完成后必须输出：
```text
TASK Completion Report

TASK ID:
Status: READY_FOR_REVIEW

Changed files:
Models added:
Enums added:
Database constraints:
Indexes:
Relationships:
Alembic migration:
Upgrade verification:
Downgrade verification:
Persistence integration test:
Code comments / docstrings:
Commands actually run:
Tests passed:
Tests failed:
Security / privacy considerations:
Deviations:
Known limitations:
git diff --stat:
git status --short:
Suggested next task:
```

不得自行写 DONE。

## 19. Codex Start Prompt
```text
当前进入 TASK-P1-001。

不要立即编码。

请完整阅读：
CODEX.md
docs/V2.1-technical-design.md
docs/V3.0-codex-execution-manual.md
docs/tasks/TASK-P1-001.md

先执行：
git rev-parse --show-toplevel
git branch --show-current
git status --short

如果工作区不干净，停止并报告。

如果工作区干净，只输出 Pre-Implementation Plan：

1. TASK-P1-001 目标
2. 预计新增/修改文件
3. ApprovalStatus / ApprovalAction / BusinessType 设计
4. ApprovalApplication 模型设计
5. ApprovalRecord 模型设计
6. MasterDataChange 模型设计
7. UUID / datetime 策略
8. Workflow Context 设计
9. FK / UNIQUE / INDEX 设计
10. ORM relationship 设计
11. Alembic migration 设计
12. PostgreSQL persistence 测试方案
13. upgrade / downgrade 验证方案
14. 中文 docstring / 注释计划
15. 架构风险
16. 明确不会实现的范围

不要编码。
等待 Architecture Review APPROVE 后再开始实现。
```
