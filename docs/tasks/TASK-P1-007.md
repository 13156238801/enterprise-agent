# TASK-P1-007 — Approve / Reject

## 0. Status

```text
TASK ID: P1-007
Name: Approve / Reject
Phase: Phase 1 — Approval Backend
Status: DESIGN FROZEN / WAITING FOR PRE-IMPLEMENTATION PLAN
```

本文件是 TASK-P1-007 的具体执行 Source of Truth。

如本文件与 V2.1 / V3.0 中历史示例存在差异，以本文件和已经合并的 P1-001～P1-006 实现为准。

开始基线：

```text
main:
737952277308f243ce1c89e80c090c9db5704b15

P1-006:
Submit Transaction + Workflow Version Binding
```

P1-007 不重新设计 P1-001～P1-006。

---

# 1. Goal

实现审批流中的两类核心人工动作：

```text
APPROVE
REJECT
```

使已经处于：

```text
ApprovalApplication(IN_APPROVAL)
```

的申请能够：

```text
APPROVE intermediate node
→ 进入 next node
→ 保持 IN_APPROVAL

APPROVE final node
→ COMPLETED

REJECT to applicant
→ DRAFT

REJECT to previous node
→ previous node
→ 保持 IN_APPROVAL
```

本 TASK 的核心是：

- 当前审批节点角色校验。
- P1-002 APPROVE / REJECT 状态机复用。
- exact bound Workflow Definition 导航。
- current task / node runtime 切换。
- previous-node reject target resolution。
- APPROVE / REJECT ApprovalRecord。
- transaction rollback。
- REST API。
- PostgreSQL integration tests。

---

# 2. Explicit Out of Scope

P1-007 禁止实现：

- withdraw
- list approvals
- approval detail API
- approval history API
- pending approvals API
- organization / department based approver resolution
- dynamic concrete assignee allocation
- admin superuser bypass
- self-approval prohibition
- final MasterDataEntity writeback
- base_entity_version stale enforcement
- optimistic-lock CAS
- Idempotency-Key
- audit_logs
- confirmation token
- Agent
- Tool Executor
- LLM
- Redis lock
- BPMN
- P1-008 或后续任务

特别注意：

最终节点 APPROVE 在本 TASK 只把：

```text
ApprovalApplication.status
→ COMPLETED
```

真正的 MasterDataEntity 写回与 stale version / optimistic lock 必须在后续 Hardening 阶段原子完成，不允许本 TASK 做一个没有并发保护的半安全写回。

---

# 3. Existing Baseline

P1-006 已有：

```text
ApprovalApplication
- workflow_code
- workflow_version
- workflow_instance_id
- current_task_id
- current_node_code
- current_node_name
- status
- submitted_at
- completed_at
- version
```

P1-006 已保证首次 submit：

```text
DRAFT
→ bind exact workflow version
→ create workflow_instance_id
→ create current_task_id
→ first node
→ IN_APPROVAL
```

P1-004 Workflow Definition：

```json
{
  "code": "supplier_change",
  "version": 1,
  "nodes": [
    {"key": "manager_review", "role": "APPROVER"},
    {"key": "data_admin_review", "role": "DATA_ADMIN"}
  ]
}
```

P1-002 已有 APPROVE / REJECT 领域规则：

```text
APPROVE:
IN_APPROVAL
+ actor in current_assignee_ids
+ is_final_approval_node
→ IN_APPROVAL / COMPLETED

REJECT:
IN_APPROVAL
+ actor in current_assignee_ids
+ reject target context
→ DRAFT / IN_APPROVAL
```

P1-007 必须复用 P1-002，不复制第二套状态转换规则。

---

# 4. Core Architecture Boundary

保持：

```text
FastAPI Route
= HTTP + coarse role gate

ApprovalService
= transaction orchestration
  + workflow/runtime integrity
  + assignee policy resolution

P1-002 State Machine
= action/state/actor/reject transition legality

WorkflowDefinition
= node navigation + required role

ApprovalRepository
= persistence/query only

AuthContext
= trusted identity + trusted roles
```

禁止：

- Route 直接修改 ORM。
- Repository commit / rollback。
- request 指定 current node。
- request 指定 target operator。
- request 指定 target task。
- LLM 决定审批权限。
- Service 自己复制整个状态机。

---

# 5. Current Assignee Policy

P1-004 node 只有：

```text
key
role
```

没有持久化 concrete assignee。

因此 P1-007 冻结两级 assignee policy：

## 5.1 Normal Role-Pool Task

如果创建当前 task 的历史记录：

```text
target_operator_id = NULL
```

则当前节点采用 role-pool：

```text
current_node.role in AuthContext.roles
→ actor eligible

否则
→ actor not current assignee
```

例如：

```text
manager_review / APPROVER

user.roles = {APPROVER}
→ eligible

user.roles = {DATA_ADMIN}
→ not eligible
```

## 5.2 Targeted Return Task

REJECT to PREVIOUS_NODE 时，本 TASK 会把该 previous node 最近一次有效 APPROVE 的 operator 记录为：

```text
target_operator_id
```

该返回 task 是 targeted task。

后续处理该 current_task_id 时：

```text
actor_id == target_operator_id
AND
current_node.role in AuthContext.roles
```

才视为 eligible。

即：

同一个角色的其他用户不能抢处理一个明确退回给原处理人的 task。

---

# 6. State Machine Integration

APPROVE / REJECT 都必须调用：

```python
decide_transition(...)
```

Service 负责先解析：

- current workflow。
- current node。
- current task assignee policy。
- final/first node。
- reject target。

然后构造：

```text
TransitionContext
```

当前 actor eligible 时：

```text
current_assignee_ids = {actor_id}
```

否则：

```text
current_assignee_ids = {}
```

最终由 P1-002 产生：

```text
ACTOR_NOT_CURRENT_ASSIGNEE
```

Service 映射为：

```text
APPROVAL_FORBIDDEN
```

禁止在 Service 直接把“状态是否合法”重新实现一遍。

---

# 7. Route Role Gate

两个动作路由都使用：

```python
require_any_role(
    UserRole.APPROVER,
    UserRole.DATA_ADMIN,
)
```

这是 coarse gate。

精确 current-node 权限仍由：

```text
Workflow node role
+
current task target_operator policy
+
P1-002 state machine
```

判断。

明确规则：

```text
APPLICANT-only → 403
ADMIN-only → 403
APPROVER → 可进入 Service
DATA_ADMIN → 可进入 Service
```

ADMIN 不隐式拥有审批角色。

如果一个用户同时具有 APPLICANT + APPROVER，本 TASK 不额外禁止其审批自己提交的申请。

“禁止 self approval”不是当前冻结业务规则，禁止自行添加。

---

# 8. Workflow Binding / Runtime Integrity

APPROVE / REJECT 前必须满足：

1. ApprovalApplication 存在。
2. business_type == MASTER_DATA_CHANGE。
3. MasterDataChange 存在。
4. approval.business_id == str(change.id)。
5. status / action legality交给 P1-002。
6. workflow_code + workflow_version 必须完整。
7. exact bound Workflow Definition 必须可读取。
8. workflow_instance_id 非空。
9. current_task_id 非空。
10. current_node_code 非空。
11. current_node_code 必须存在于 exact bound Workflow Definition。

任何 workflow snapshot corruption / missing exact version / runtime missing / node unknown：

```text
APPROVAL_DATA_INTEGRITY_ERROR
```

绝不能：

```text
fallback to active workflow
```

P1-007 永远操作 P1-006 已绑定的 exact workflow version。

---

# 9. APPROVE — Intermediate Node

例如：

```text
manager_review
→ data_admin_review
```

成功后：

```text
status = IN_APPROVAL

workflow_code unchanged
workflow_version unchanged
workflow_instance_id unchanged

current_task_id = new str(uuid4())
current_node_code = next_node.key
current_node_name = None

submitted_at unchanged
completed_at = None
version unchanged
```

新的 next-node task：

```text
target_operator_id = None
```

表示普通 role-pool task。

---

# 10. APPROVE — Final Node

如果：

```text
current node == final workflow node
```

则 P1-002 决策：

```text
IN_APPROVAL
→ COMPLETED
```

成功后：

```text
status = COMPLETED

workflow_code/version unchanged
workflow_instance_id retained

current_task_id = None
current_node_code = None
current_node_name = None

completed_at = datetime.now(UTC)

submitted_at unchanged
version unchanged
```

ApprovalRecord 的 target runtime：

```text
target_task_id = None
target_node_code = None
target_node_name = None
target_operator_id = None
```

P1-007 不修改 MasterDataEntity。

---

# 11. REJECT Request

新增：

```json
{
  "target": "APPLICANT",
  "comment": "请补充资料"
}
```

或：

```json
{
  "target": "PREVIOUS_NODE",
  "comment": "请前序节点重新确认"
}
```

只允许：

```text
APPLICANT
PREVIOUS_NODE
```

客户端禁止指定：

- target_node_code
- target_task_id
- target_operator_id
- workflow_code
- workflow_version
- status

这些全部由服务端可信状态推导。

---

# 12. Comment Validation

## 12.1 APPROVE

approve comment：

```text
optional
```

规则：

- None → None。
- str → strip。
- blank string → None。
- 最大 2000 chars。
- non-string 或超长 → APPROVAL_INVALID_REQUEST。

## 12.2 REJECT

reject comment：

```text
required
```

规则：

- 必须为 str。
- strip 后非空。
- 1～2000 chars。
- 非法 → APPROVAL_INVALID_REQUEST。

业务校验放 Service，保证核心错误走 AppError / ErrorEnvelope。

Pydantic structural errors（例如 extra forbidden field）沿用现有项目行为，不在本 TASK 全局重写 RequestValidationError。

---

# 13. REJECT to APPLICANT

成功后：

```text
status = DRAFT

workflow_code/version retained
workflow_instance_id retained

current_task_id = None
current_node_code = None
current_node_name = None

submitted_at retained
completed_at = None
version unchanged
```

ApprovalRecord target：

```text
target_task_id = None
target_node_code = None
target_node_name = None
target_operator_id = applicant_id
```

原因：

申请人不是 workflow approval task，因此不生成新的审批 task id。

后续 applicant 修改草稿仍走 P1-005 SAVE。

再次 submit 仍走 P1-006：

```text
exact bound workflow version
+
new workflow_instance_id
+
new first task
```

---

# 14. REJECT to PREVIOUS_NODE

服务端必须从 exact bound workflow：

```text
previous_node(current_node_code)
```

推导目标。

客户端不能指定 previous node。

## 14.1 First Node

如果当前 node 是 first node：

```text
PREVIOUS_NODE
```

不可用。

返回：

```text
APPROVAL_REJECT_TARGET_UNAVAILABLE
HTTP 409
```

## 14.2 Resolve Previous Operator

如果存在 previous node，Repository 查询：

最近一条满足：

```text
approval_id == current approval
action == APPROVE
from_node_code == previous_node.key
target_node_code == current_node.key
```

的 ApprovalRecord。

它的：

```text
operator_id
```

就是本次 targeted return 的：

```text
target_operator_id
```

如果按当前 runtime 来看理论上应该存在 previous approval history，但数据库没有：

```text
APPROVAL_DATA_INTEGRITY_ERROR
```

不得自动选择任意同角色用户。

---

# 15. REJECT to PREVIOUS_NODE Runtime

成功后：

```text
status = IN_APPROVAL

workflow_code/version unchanged
workflow_instance_id unchanged

current_task_id = new str(uuid4())
current_node_code = previous_node.key
current_node_name = None

submitted_at unchanged
completed_at = None
version unchanged
```

ApprovalRecord：

```text
target_task_id = new current_task_id
target_node_code = previous_node.key
target_node_name = None
target_operator_id = resolved previous operator
```

该新 task 为 targeted return task。

---

# 16. Current Task Assignment Lookup

ApprovalRepository 增加等价能力：

```text
get_current_task_target_operator(
    approval_id,
    current_task_id
)
```

语义：

查询创建当前 task 的最近历史记录：

```text
target_task_id == ApprovalApplication.current_task_id
```

读取其：

```text
target_operator_id
```

如果：

```text
target_operator_id is None
```

→ normal role-pool task。

如果：

```text
target_operator_id != None
```

→ targeted task。

如果 current_task_id 存在，但找不到创建该 task 的历史记录：

```text
APPROVAL_DATA_INTEGRITY_ERROR
```

这样 P1-006 SUBMIT / P1-007 intermediate APPROVE 的 role-pool task，以及 previous-node reject 的 targeted task 可以使用同一解析逻辑。

---

# 17. APPROVE ApprovalRecord

所有成功 APPROVE：

```text
action = APPROVE
operator_id = authenticated actor

from_status = decision.from_status
to_status = decision.to_status

from_task_id = old current_task_id
from_node_code = old current_node_code
from_node_name = old current_node_name
```

Intermediate：

```text
target_task_id = new task
target_node_code = next node
target_node_name = None
target_operator_id = None
```

Final：

```text
all target runtime fields = None
```

comment 保存规范化后的 approve comment。

ApprovalRecord append-only。

---

# 18. REJECT ApprovalRecord

所有成功 REJECT：

```text
action = REJECT
operator_id = authenticated actor

from_status = decision.from_status
to_status = decision.to_status

from_task_id = old current_task_id
from_node_code = old current_node_code
from_node_name = old current_node_name

comment = normalized required comment
```

Reject applicant：

```text
target_task_id = None
target_node_code = None
target_node_name = None
target_operator_id = applicant_id
```

Reject previous：

```text
target_task_id = new task id
target_node_code = previous node
target_node_name = None
target_operator_id = resolved previous operator
```

---

# 19. API

新增：

```http
POST /api/v1/approvals/{approval_id}/approve
Authorization: Bearer <token>
```

Body 可省略，也可：

```json
{
  "comment": "同意"
}
```

新增：

```http
POST /api/v1/approvals/{approval_id}/reject
Authorization: Bearer <token>
Content-Type: application/json
```

Body：

```json
{
  "target": "APPLICANT | PREVIOUS_NODE",
  "comment": "..."
}
```

---

# 20. Response

建议复用统一动作响应：

```json
{
  "approval_id": "uuid",
  "action": "APPROVE",
  "status": "IN_APPROVAL",
  "workflow_code": "supplier_change",
  "workflow_version": 1,
  "workflow_instance_id": "uuid-string",
  "current_task_id": "uuid-string-or-null",
  "current_node_code": "data_admin_review-or-null",
  "current_node_role": "DATA_ADMIN-or-null",
  "completed_at": null
}
```

Reject applicant / final approve：

```text
current_task_id = null
current_node_code = null
current_node_role = null
```

不要返回：

- Auth token。
- password/hash。
- full workflow JSON。
- internal stack。
- target operator identity。

P1-009 再设计完整 approval detail/history view。

---

# 21. Approval Errors

扩展现有 ApprovalErrorCode。

至少新增：

| Code | HTTP | Meaning |
|---|---:|---|
| APPROVAL_INVALID_REQUEST | 422 | comment / reject target 不合法 |
| APPROVAL_REJECT_TARGET_UNAVAILABLE | 409 | 当前节点没有 previous node |

继续复用：

| Code | HTTP |
|---|---:|
| APPROVAL_NOT_FOUND | 404 |
| APPROVAL_INVALID_TRANSITION | 409 |
| APPROVAL_FORBIDDEN | 403 |
| APPROVAL_DATA_INTEGRITY_ERROR | 500 |

映射：

```text
INVALID_STATUS_ACTION
→ APPROVAL_INVALID_TRANSITION

ACTOR_NOT_CURRENT_ASSIGNEE
→ APPROVAL_FORBIDDEN

其他由 Service 正确构造上下文后仍产生的
ApprovalTransitionError
→ APPROVAL_DATA_INTEGRITY_ERROR
```

WorkflowDefinitionError：

```text
→ APPROVAL_DATA_INTEGRITY_ERROR
```

所有 AppError 保留 request_id。

401 继续保留：

```text
WWW-Authenticate: Bearer
```

---

# 22. Service Structure

继续扩展现有：

```text
ApprovalService
```

新增至少：

```text
approve(...)
reject(...)
```

允许提取 private helpers，例如：

```text
_load_action_context
_load_bound_workflow
_resolve_current_assignment
_map_transition_error
_validate_comment
```

但不要为了 P1-007 引入新的 workflow engine abstraction。

目标是减少 submit / approve / reject 之间重复，同时不进行与本 TASK 无关的大重构。

---

# 23. Repository Structure

继续扩展：

```text
ApprovalRepository
```

至少支持：

- get_application_for_action
- save_transition
- resolve current task target operator
- find previous-node latest approver

Repository：

- injected AsyncSession。
- no commit。
- no rollback。
- no FastAPI。
- no AuthContext。
- no HTTP mapping。

---

# 24. Transaction Boundary

APPROVE：

```text
BEGIN
  load approval + change
  validate binding/runtime
  load exact workflow
  resolve current node + assignment
  decide_transition(APPROVE)
  capture old runtime
  if intermediate:
      create new task
      move next node
  if final:
      clear current task/node
      set completed_at
  append APPROVE record
  flush
COMMIT
```

REJECT：

```text
BEGIN
  load approval + change
  validate binding/runtime
  load exact workflow
  resolve current node + assignment
  resolve applicant/previous target
  decide_transition(REJECT)
  capture old runtime
  update status/runtime
  append REJECT record
  flush
COMMIT
```

任何一步失败：

```text
ROLLBACK
```

---

# 25. Workflow Instance Semantics

一个 submit cycle：

```text
SUBMIT
→ workflow_instance_id = X
```

在该 cycle 内：

```text
approve next node
reject previous node
approve again
```

都保持：

```text
workflow_instance_id = X
```

只有未来：

```text
reject applicant / withdraw
→ DRAFT
→ resubmit
```

由 P1-006 生成新的 workflow_instance_id。

P1-007 reject applicant 时保留旧 instance id，直到 resubmit 覆盖。

---

# 26. Task ID Semantics

每个新的审批 task 都生成：

```text
str(uuid4())
```

以下生成新 task：

- intermediate APPROVE → next node。
- REJECT PREVIOUS_NODE → previous node。

以下不生成 task：

- final APPROVE。
- REJECT APPLICANT。

task id 永远由服务端生成。

---

# 27. Master Data Boundary

P1-007 无论 intermediate/final approve 或 reject，都不得修改：

```text
MasterDataEntity.data_json
MasterDataEntity.version

MasterDataChange.before_data
MasterDataChange.form_data
MasterDataChange.change_items
MasterDataChange.base_entity_version
```

最终写回后续必须与 optimistic lock/stale check 一起设计。

---

# 28. Approval Version Boundary

P1-007 不修改：

```text
ApprovalApplication.version
```

原因：

完整 CAS/version increment 统一留给 P1-010。

禁止只给 approve/reject 半实现 optimistic lock。

---

# 29. No Migration

P1-007 预期：

```text
NO DATABASE SCHEMA CHANGE
NO ALEMBIC MIGRATION
```

现有字段已经足够：

- ApprovalApplication current workflow/runtime。
- ApprovalRecord from/target/operator/comment。

如果实现过程中发现必须改 schema：

停止。

提交 Architecture Deviation。

未经 Review 不得生成 migration。

---

# 30. Required Tests — API Role / Validation

1. unauthenticated approve → 401 + Bearer。
2. unauthenticated reject → 401 + Bearer。
3. APPLICANT-only approve/reject → 403。
4. ADMIN-only approve/reject → 403。
5. APPROVER 可以进入 approve/reject Service。
6. DATA_ADMIN 可以进入 approve/reject Service。
7. route coarse gate 不等于 current-node authorization。
8. invalid approve comment → APPROVAL_INVALID_REQUEST。
9. invalid reject target → APPROVAL_INVALID_REQUEST。
10. blank reject comment → APPROVAL_INVALID_REQUEST。
11. overlong reject comment → APPROVAL_INVALID_REQUEST。
12. extra target_node/operator fields不能控制服务端 runtime。
13. ErrorEnvelope includes request_id。

---

# 31. Required Tests — Current Assignment

14. manager_review required role APPROVER。
15. DATA_ADMIN-only actor at manager_review → FORBIDDEN。
16. data_admin_review required role DATA_ADMIN。
17. APPROVER-only actor at data_admin_review → FORBIDDEN。
18. normal task target_operator NULL → matching role pool user eligible。
19. targeted return task → only target_operator eligible。
20. same-role but different user on targeted return → FORBIDDEN。
21. targeted operator missing required role → FORBIDDEN。
22. current_task_id exists but no creating history → DATA_INTEGRITY。

---

# 32. Required Tests — APPROVE

23. unknown approval → APPROVAL_NOT_FOUND。
24. broken MasterData linkage → DATA_INTEGRITY。
25. non-IN_APPROVAL → INVALID_TRANSITION。
26. missing/incomplete workflow binding → DATA_INTEGRITY。
27. missing exact bound workflow → DATA_INTEGRITY。
28. corrupt bound workflow → DATA_INTEGRITY。
29. missing runtime fields → DATA_INTEGRITY。
30. unknown current_node_code → DATA_INTEGRITY。
31. APPROVE uses P1-002 decide_transition。
32. intermediate approve remains IN_APPROVAL。
33. intermediate approve keeps workflow_instance_id。
34. intermediate approve creates new task id。
35. intermediate approve moves exact next node。
36. intermediate approve target_operator NULL。
37. final approve becomes COMPLETED。
38. final approve retains workflow_instance_id。
39. final approve clears current task/node。
40. final approve sets timezone-aware completed_at。
41. approve leaves submitted_at unchanged。
42. approve leaves workflow binding unchanged。
43. approve leaves ApprovalApplication.version unchanged。
44. approve appends exactly one APPROVE record。
45. record from runtime equals old runtime。
46. record target runtime equals new runtime。
47. final record target runtime all NULL。
48. approve comment normalized/stored。
49. MasterData unchanged。

---

# 33. Required Tests — REJECT APPLICANT

50. reject applicant uses P1-002。
51. status → DRAFT。
52. workflow binding retained。
53. workflow_instance_id retained。
54. current task/node cleared。
55. submitted_at retained。
56. completed_at None。
57. version unchanged。
58. record action = REJECT。
59. target_operator_id = applicant_id。
60. target task/node NULL。
61. required comment stored normalized。
62. MasterData unchanged。
63. applicant can later SAVE through existing P1-005 rule。
64. future resubmit continues exact bound workflow version through existing P1-006 behavior。

---

# 34. Required Tests — REJECT PREVIOUS_NODE

65. first node reject previous → REJECT_TARGET_UNAVAILABLE。
66. second node resolves previous node from Workflow Definition。
67. previous operator derived from latest matching APPROVE history。
68. missing expected previous APPROVE history → DATA_INTEGRITY。
69. status stays IN_APPROVAL。
70. workflow_instance_id unchanged。
71. new task id generated。
72. current node moves previous。
73. target_operator_id = resolved previous operator。
74. comment stored。
75. returned task becomes targeted。
76. unrelated same-role user cannot process returned task。
77. original previous operator can process returned task if role still valid。
78. later active workflow version change does not affect bound-version navigation。

---

# 35. Required Tests — Transaction / History

79. ApprovalRecord append-only。
80. post-flush injected APPROVE failure → full rollback。
81. rollback restores status。
82. rollback restores current runtime。
83. rollback restores completed_at。
84. rollback removes new APPROVE record。
85. post-flush injected REJECT failure → full rollback。
86. rollback removes new REJECT record。
87. no partial task switch survives。
88. no MasterData mutation survives because none should occur。

---

# 36. Regression

89. P1-001 tests green。
90. P1-002 tests green。
91. P1-003 tests green。
92. P1-004 tests green。
93. P1-005 tests green。
94. P1-006 tests green。
95. backend CI green。
96. frontend CI green。
97. existing migrations stay no-drift。
98. demo user/workflow/master-data seeds green。

---

# 37. CI

P1-007 不需要 migration CI 变化。

保持现有 CI：

```text
Ruff
format
alembic upgrade/check
P1-006 downgrade/re-upgrade
P1-005 downgrade/backfill
P1-004 downgrade regression
full PostgreSQL pytest
demo seeds
frontend lint/typecheck/test/build
```

只需要新增 P1-007 tests。

如果 Codex 修改 CI：

必须说明必要性。

无必要不得改。

---

# 38. Suggested Files

预计修改：

```text
backend/app/approval/errors.py
backend/app/approval/repository.py
backend/app/approval/schemas.py
backend/app/approval/service.py
backend/app/api/errors.py
backend/app/api/v1/approvals.py
backend/tests/test_approval_actions.py
```

允许按项目风格将测试继续放入：

```text
backend/tests/test_approval_submit.py
```

但优先建议新建：

```text
test_approval_actions.py
```

保持 submit 与 approve/reject 测试职责清晰。

预计不修改：

```text
backend/app/models/**
backend/alembic/**
backend/app/domain/approval/state_machine.py
backend/app/domain/approval/context.py
backend/app/domain/workflow/**
frontend/**
.github/workflows/ci.yml
```

如必须修改上述冻结文件：

Pre-Implementation Plan 中必须明确说明理由。

---

# 39. Frozen Decisions

1. P1-007 = APPROVE + REJECT。
2. 不实现 withdraw。
3. 不实现 final MasterData writeback。
4. 不实现 optimistic lock。
5. 不实现 idempotency。
6. 不实现 confirmation token。
7. exact bound workflow only。
8. never fallback to active workflow。
9. APPROVER / DATA_ADMIN 是 route coarse gate。
10. exact node role 决定正常 task eligibility。
11. role-pool task target_operator_id = None。
12. reject previous creates targeted return task。
13. targeted return only target operator can act。
14. target operator additionally must still hold required node role。
15. previous operator derives from latest matching APPROVE history。
16. first node cannot reject previous。
17. reject applicant → DRAFT。
18. reject previous → IN_APPROVAL。
19. intermediate approve → IN_APPROVAL next node。
20. final approve → COMPLETED。
21. workflow_instance_id stays constant inside one submit cycle。
22. intermediate / previous-reject generate new task IDs。
23. final approve / reject applicant clear current task/node。
24. reject applicant retains workflow_instance_id until resubmit。
25. submitted_at never changes during approve/reject。
26. completed_at only final approve sets。
27. current_node_name remains None for newly generated tasks。
28. ApprovalApplication.version unchanged。
29. MasterData snapshots unchanged。
30. ApprovalRecord append-only。
31. comments max 2000。
32. reject comment required。
33. approve comment optional。
34. no DB migration。
35. no P1-008 implementation。

任何冻结项要改变：

停止并提交 Architecture Deviation。

---

# 40. Definition of Done

- [ ] approve API
- [ ] reject API
- [ ] approve/reject schemas
- [ ] comment validation
- [ ] invalid request error
- [ ] reject target unavailable error
- [ ] coarse role gate
- [ ] exact node role validation
- [ ] targeted-return assignment policy
- [ ] current task target lookup
- [ ] previous approver lookup
- [ ] exact bound workflow navigation
- [ ] intermediate approve
- [ ] final approve
- [ ] reject applicant
- [ ] reject previous
- [ ] P1-002 reuse
- [ ] ApprovalRecord append
- [ ] runtime transitions
- [ ] transaction rollback
- [ ] MasterData immutability
- [ ] Approval version unchanged
- [ ] no migration
- [ ] full PostgreSQL tests
- [ ] P1-001～P1-006 regression
- [ ] backend CI
- [ ] frontend CI
- [ ] no P1-008

---

# 41. Completion Report

Codex 最终必须输出：

```text
TASK Completion Report

TASK ID:
TASK-P1-007

Status:
READY_FOR_REVIEW

Changed files:

Approve API:

Reject API:

Request validation:

Role gate:

Current-node authorization:

Role-pool assignment:

Targeted-return assignment:

Bound workflow navigation:

Intermediate approve:

Final approve:

Reject to applicant:

Reject to previous node:

Previous operator resolution:

P1-002 reuse:

ApprovalRecord:

Transaction rollback:

MasterData immutability:

Approval version behavior:

Migration:
NONE

Schema drift:

PostgreSQL integration tests:

Regression tests:

CI result:

Commands actually run:

Tests passed:

Tests failed:

Warnings:

Deviations:

Known limitations:

git diff --stat:

git status --short:

Commit SHA:

Push result:

PR URL / status:

Suggested next task:
```

不得写 DONE。

---

# 42. Codex Pre-Implementation Plan Prompt

```text
当前进入 TASK-P1-007 — Approve / Reject。

本轮不要编码。

Source of Truth：

- CODEX.md
- docs/V2.1-technical-design.md
- docs/V3.0-codex-execution-manual.md
- docs/tasks/TASK-P1-007.md

P1-007 具体范围以：

docs/tasks/TASK-P1-007.md

为最高优先级。

开始前执行：

git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short

预期：

branch:
task/p1-007

HEAD 必须包含本任务文档提交，并基于：

main:
737952277308f243ce1c89e80c090c9db5704b15

工作区必须干净。

如果不满足：

停止。
不要 reset --hard。
不要自行 merge / rebase。
不要开始实现。
报告实际状态。

然后完整阅读：

- CODEX.md
- docs/V2.1-technical-design.md
- docs/V3.0-codex-execution-manual.md
- docs/tasks/TASK-P1-007.md
- backend/app/domain/approval/**
- backend/app/domain/workflow/**
- backend/app/approval/**
- backend/app/api/**
- backend/app/auth/**
- backend/app/master_data/**
- backend/app/workflow/**
- backend/app/models/**
- backend/tests/**
- .github/workflows/ci.yml

本轮只输出：

# Pre-Implementation Plan — TASK-P1-007

至少覆盖：

1. TASK 目标和边界
2. 新增 / 修改文件
3. 为什么 P1-007 不需要 migration
4. approve API
5. reject API
6. approve comment validation
7. reject target/comment validation
8. invalid request ErrorEnvelope
9. route coarse role gate
10. current node exact role authorization
11. normal role-pool task 定义
12. targeted-return task 定义
13. 如何从 current_task_id 找 target_operator
14. targeted operator + required role 双重条件
15. P1-002 current_assignee_ids 如何构造
16. ACTOR_NOT_CURRENT_ASSIGNEE mapping
17. exact bound workflow loading
18. 为什么绝不 fallback active workflow
19. runtime integrity checks
20. intermediate approve
21. final approve
22. workflow_instance_id semantics
23. task id semantics
24. completed_at semantics
25. submitted_at 为什么不改
26. reject applicant
27. reject previous
28. first-node previous reject handling
29. previous operator 如何从 APPROVE history 推导
30. history 缺失为何是 DATA_INTEGRITY
31. ApprovalRecord from/target 字段
32. approve/reject comments
33. Repository 新查询
34. ApprovalService transaction boundary
35. rollback tests
36. MasterData 为什么不写回
37. ApprovalApplication.version 为什么不改
38. sequential behavior
39. concurrency limitation
40. PostgreSQL integration tests
41. API tests
42. regression tests
43. CI 是否需要变化
44. 如何保证不进入 P1-008
45. Architecture risks / reviewer questions
46. Out of Scope

不要编码。
不要生成 migration。
不要修改文件。
不要安装依赖。

等待 Architecture Review APPROVE。
```
