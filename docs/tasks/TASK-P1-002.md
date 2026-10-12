# TASK-P1-002 — Approval State Machine 与业务规则校验

## 1. Task Metadata

- **TASK ID**: `TASK-P1-002`
- **Phase**: Phase 1 — Approval Backend
- **Priority**: P0
- **Status**: READY
- **Depends on**: `TASK-P1-001`

## 2. Goal

将已确认的审批状态流转规则实现为可独立测试、与 API/数据库解耦的纯领域状态机。

原则：

> LLM、API、前端、Tool 都不能自己决定审批状态怎么跳转。所有状态合法性判断统一收口到 Approval State Machine。

## 3. 已确认的业务规则

### Submit

```text
DRAFT --SUBMIT--> IN_APPROVAL
```

### Approve — 中间节点

```text
IN_APPROVAL --APPROVE--> IN_APPROVAL
```

### Approve — 最终节点

```text
IN_APPROVAL --APPROVE--> COMPLETED
```

### Reject — 驳回提交人

```text
IN_APPROVAL --REJECT--> DRAFT
```

### Reject — 驳回前序审批节点

```text
IN_APPROVAL --REJECT--> IN_APPROVAL
```

如需统计第几次驳回，应统计 `ApprovalRecord.action == REJECT`，不在申请主表增加 `reject_count`。

### Withdraw

仅当：

1. 当前状态为 `IN_APPROVAL`
2. 操作者是申请人本人
3. 当前仍处于第一个审批节点
4. 尚未有前序审批节点完成审批

才允许：

```text
IN_APPROVAL --WITHDRAW--> DRAFT
```

撤回后可修改并再次提交。

## 4. Status / Action Matrix

| Current Status | Action | Allowed | Next Status | Additional Conditions |
|---|---|---:|---|---|
| DRAFT | SAVE | Yes | DRAFT | 操作者必须是申请人 |
| DRAFT | SUBMIT | Yes | IN_APPROVAL | 操作者必须是申请人 |
| DRAFT | APPROVE | No | - | 非审批中状态 |
| DRAFT | REJECT | No | - | 非审批中状态 |
| DRAFT | WITHDRAW | No | - | 尚未进入审批 |
| IN_APPROVAL | SAVE | No | - | 审批中不可直接修改 |
| IN_APPROVAL | SUBMIT | No | - | 已处于审批流程 |
| IN_APPROVAL | APPROVE | Yes | IN_APPROVAL / COMPLETED | 当前节点处理人；由是否最终节点决定 |
| IN_APPROVAL | REJECT | Yes | DRAFT / IN_APPROVAL | 当前节点处理人；由驳回目标决定 |
| IN_APPROVAL | WITHDRAW | Conditional | DRAFT | 申请人本人 + 第一个审批节点 |
| COMPLETED | SAVE/SUBMIT/APPROVE/REJECT/WITHDRAW | No | - | 已结束 |
| VOIDED | SAVE/SUBMIT/APPROVE/REJECT/WITHDRAW | No | - | 已作废 |

### CREATE / VOID

- `CREATE` 是创建申请时的审计动作，不作为已有申请之间的状态迁移。
- 已确认存在 `VOIDED` 状态，但当前没有足够依据确认“谁在什么状态下可以作废”。
- 因此 P1-002 不得猜测 VOID 迁移规则。
- 第一版状态机对 `VOID` 返回“规则未启用/不支持”。

## 5. Actor Rules

### Applicant Actions

申请人本人可以：

- `SAVE`：仅 DRAFT
- `SUBMIT`：仅 DRAFT
- `WITHDRAW`：仅 IN_APPROVAL 且当前是首个审批节点

申请人身份本身不授予 `APPROVE` / `REJECT` 权限。

### Current Approver Actions

当前节点处理人可以：

- `APPROVE`
- `REJECT`

前提：

- 当前状态为 `IN_APPROVAL`
- actor 属于当前节点处理人集合

P1-002 不实现用户/角色/权限表，只接收已经解析好的：

```text
actor_id
applicant_id
current_assignee_ids
```

## 6. Transition Context

建议定义纯领域 Context：

```python
@dataclass(frozen=True)
class TransitionContext:
    actor_id: str
    applicant_id: str
    current_assignee_ids: frozenset[str] = frozenset()
    is_first_approval_node: bool = False
    is_final_approval_node: bool = False
    reject_target_type: RejectTargetType | None = None
    target_node_code: str | None = None
    target_operator_id: str | None = None
```

允许等价设计，但不得依赖 FastAPI Request、SQLAlchemy Session 或数据库查询。

## 7. Reject Target

建议新增：

```text
RejectTargetType
- APPLICANT
- PREVIOUS_NODE
```

### APPLICANT

```text
IN_APPROVAL --REJECT--> DRAFT
```

### PREVIOUS_NODE

```text
IN_APPROVAL --REJECT--> IN_APPROVAL
```

必须提供：

```text
target_node_code
target_operator_id
```

状态机只验证上下文完整性，不查询真实流程引擎。

## 8. Transition Result

状态机不直接写数据库。

建议返回不可变结果：

```python
@dataclass(frozen=True)
class TransitionDecision:
    action: ApprovalAction
    from_status: ApprovalStatus
    to_status: ApprovalStatus
```

如确有必要可增加少量明确字段，但禁止混入 ORM、Session、数据库写入或 API Response。

## 9. Error Model

不要只抛裸 `ValueError("invalid")`。

建议建立：

```text
ApprovalTransitionError
```

至少提供稳定 error code：

```text
INVALID_STATUS_ACTION
ACTOR_NOT_APPLICANT
ACTOR_NOT_CURRENT_ASSIGNEE
WITHDRAW_NOT_FIRST_NODE
APPROVE_NODE_CONTEXT_INVALID
REJECT_TARGET_REQUIRED
REJECT_TARGET_CONTEXT_INVALID
VOID_RULE_NOT_ENABLED
CREATE_NOT_STATE_TRANSITION
```

异常 message 可中文，测试重点验证稳定 code。

## 10. Package Structure

建议：

```text
backend/app/domain/
├── __init__.py
└── approval/
    ├── __init__.py
    ├── state_machine.py
    ├── context.py
    └── errors.py
```

实现较小时允许适当合并，但必须保持：

```text
Domain State Machine
≠ ORM
≠ API
≠ Service
```

## 11. Pure Domain Requirement

禁止：

```text
FastAPI / Request
SQLAlchemy Session
PostgreSQL
Redis
外部 Workflow API
LLM
Tool
```

状态机必须是确定性纯业务逻辑。

## 12. Required Tests

### DRAFT

1. applicant SAVE → DRAFT
2. applicant SUBMIT → IN_APPROVAL
3. non-applicant SAVE → reject
4. non-applicant SUBMIT → reject
5. DRAFT APPROVE → reject
6. DRAFT REJECT → reject
7. DRAFT WITHDRAW → reject

### IN_APPROVAL — APPROVE

8. current approver + intermediate node APPROVE → IN_APPROVAL
9. current approver + final node APPROVE → COMPLETED
10. non-current approver APPROVE → reject

### IN_APPROVAL — REJECT

11. current approver REJECT to APPLICANT → DRAFT
12. current approver REJECT to PREVIOUS_NODE → IN_APPROVAL
13. PREVIOUS_NODE without target_node_code → reject
14. PREVIOUS_NODE without target_operator_id → reject
15. non-current approver REJECT → reject
16. missing reject_target_type → reject

### IN_APPROVAL — WITHDRAW

17. applicant + first node WITHDRAW → DRAFT
18. applicant + non-first node WITHDRAW → reject
19. non-applicant + first node WITHDRAW → reject

### Terminal State

20. COMPLETED 的 SAVE/SUBMIT/APPROVE/REJECT/WITHDRAW 全部拒绝
21. VOIDED 的 SAVE/SUBMIT/APPROVE/REJECT/WITHDRAW 全部拒绝

### Unsupported Actions

22. CREATE 作为已有申请状态迁移动作时拒绝
23. VOID 返回 `VOID_RULE_NOT_ENABLED`

### Determinism / Boundary

24. State Machine 测试不连接 PostgreSQL / Redis
25. State Machine 不依赖 FastAPI / SQLAlchemy Session
26. 同样输入得到同样 Decision / error code

## 13. Comments / Docstrings

- 所有函数、方法、dataclass、Enum、异常类写中文 docstring。
- 关键规则解释“为什么”，尤其：
  - WITHDRAW 为什么只能首节点
  - REJECT 为什么可能 status 不变
  - CREATE 为什么不是状态迁移
  - VOID 为什么暂不实现
- 不写机械逐行注释。

## 14. Out of Scope

禁止实现：

- REST API
- Approval Service 数据库事务
- 更新 ApprovalApplication
- 插入 ApprovalRecord
- Workflow Adapter
- 查询真实下一节点
- 用户/角色/权限数据库
- 幂等
- optimistic locking
- HITL
- Tool
- Agent
- RAG
- MCP
- Redis
- 前端

P1-002 只回答：

> 给定当前状态、动作和已经解析好的业务上下文，这个动作是否合法？合法的话下一状态是什么？

## 15. Verification Commands

```powershell
cd backend

uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run alembic check
uv run alembic current

git diff --check
git status --short
```

P1-002 不新增 migration，数据库应继续保持 P1-001 的 head。

## 16. Definition of Done

- [ ] 状态机是纯领域代码
- [ ] DRAFT SAVE/SUBMIT 规则正确
- [ ] APPROVE 中间节点/最终节点区分正确
- [ ] REJECT 提交人/前序节点区分正确
- [ ] WITHDRAW 只允许申请人首节点撤回
- [ ] COMPLETED / VOIDED 为终态
- [ ] CREATE 不作为普通状态迁移
- [ ] VOID 不猜测规则
- [ ] actor 校验正确
- [ ] reject target context 校验正确
- [ ] 使用稳定 domain error code
- [ ] 不依赖 FastAPI / DB / Redis / LLM
- [ ] 每个函数/类/test 有中文 docstring
- [ ] pytest / Ruff / git diff --check 通过
- [ ] alembic check 无 schema drift
- [ ] 未开始下一 TASK

## 17. Completion Report

```text
TASK Completion Report

TASK ID:
Status: READY_FOR_REVIEW

Changed files:
Domain types added:
State machine rules implemented:
Actor validations:
Reject validations:
Withdraw validations:
Unsupported rules:
Error codes:
Commands actually run:
Tests passed:
Tests failed:
Architecture boundary verification:
Code comments / docstrings:
Deviations:
Known limitations:
git diff --stat:
git status --short:
Suggested next task:
```

不得自行写 `DONE`。

## 18. Codex Start Prompt

```text
当前进入 TASK-P1-002。

不要立即编码。

请完整阅读：
CODEX.md
docs/V2.1-technical-design.md
docs/V3.0-codex-execution-manual.md
docs/tasks/TASK-P1-002.md

先执行：
git rev-parse --show-toplevel
git branch --show-current
git status --short

如果工作区不干净，停止并报告。

如果工作区干净，只输出 Pre-Implementation Plan：

1. TASK-P1-002 目标
2. 预计新增/修改文件
3. State Machine API 设计
4. TransitionContext 设计
5. TransitionDecision 设计
6. RejectTargetType 设计
7. Domain Error / Error Code 设计
8. DRAFT 规则
9. APPROVE 规则
10. REJECT 规则
11. WITHDRAW 规则
12. Terminal State 规则
13. CREATE / VOID 处理
14. 测试矩阵
15. Pure Domain 边界如何保证
16. 中文 docstring / 注释计划
17. 明确不会实现的范围

不要编码。
等待 Architecture Review APPROVE 后再开始实现。
```
