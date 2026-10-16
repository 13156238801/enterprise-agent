# TASK-P1-008 — Withdraw

## 0. Status

```text
TASK ID: P1-008
Name: Withdraw
Phase: Phase 1 — Approval Backend
Status: DESIGN FROZEN / WAITING FOR PRE-IMPLEMENTATION PLAN
```

本文件是 TASK-P1-008 的具体执行 Source of Truth。

如本文件与 V2.1 / V3.0 中历史示例存在差异，以本文件和已经合并的 P1-001～P1-007 实现为准。

开始基线：

```text
main:
f6112c3f94afef9255f75d4ff8e4c28cd7f0514b

P1-007:
Approve / Reject
```

P1-008 不重新设计 P1-001～P1-007。

---

# 1. Goal

实现审批申请的申请人撤回能力：

```text
IN_APPROVAL
+
actor == applicant
+
current node is first workflow node
↓
WITHDRAW
↓
DRAFT
```

撤回后申请人可以继续：

```text
SAVE
→ SUBMIT
```

重新进入审批。

本 TASK 核心：

- 复用 P1-002 WITHDRAW 状态机。
- 只允许申请人本人撤回。
- 只允许“当前仍处于首个审批节点”时撤回。
- 使用 P1-006 已绑定的 exact workflow version 判断 first node。
- 撤回后清理当前审批 task/node，但保留流程绑定和旧 workflow instance，等待下一次 resubmit 覆盖。
- append WITHDRAW ApprovalRecord。
- transaction rollback。
- REST API。
- PostgreSQL integration tests。

---

# 2. Explicit Out of Scope

P1-008 禁止实现：

- approve/reject 新规则
- pending/list/detail/history API
- final MasterData writeback
- organization / department approver resolution
- dynamic assignee
- self-approval prohibition
- optimistic lock / CAS
- Idempotency-Key
- audit_logs
- confirmation token
- Agent / Tool / LLM / MCP
- Redis lock
- BPMN
- P1-009
- P1-010

不得顺手重构整个 ApprovalService。

---

# 3. Existing Domain Rule

P1-002 已冻结：

```text
WITHDRAW only allowed from IN_APPROVAL
actor must equal applicant
current node must be first approval node
to_status = DRAFT
```

现有稳定错误：

```text
INVALID_STATUS_ACTION
ACTOR_NOT_APPLICANT
WITHDRAW_NOT_FIRST_NODE
```

P1-008 必须复用：

```python
decide_transition(
    current_status=approval.status,
    action=ApprovalAction.WITHDRAW,
    context=TransitionContext(...),
)
```

不得复制：

```python
if approval.status != IN_APPROVAL:
...
if actor != applicant:
...
if current_node != first_node:
...
```

作为第二套业务状态机。

Service 可以做数据完整性准备与错误映射，但最终 transition legality 由 P1-002 决定。

---

# 4. API

新增：

```http
POST /api/v1/approvals/{approval_id}/withdraw
Authorization: Bearer <token>
```

Body 可省略，也可以：

```json
{
  "comment": "需要修改申请内容"
}
```

Route coarse gate：

```python
require_any_role(UserRole.APPLICANT)
```

明确：

```text
APPLICANT → 可进入 Service

APPROVER-only → 403
DATA_ADMIN-only → 403
ADMIN-only → 403
```

ADMIN 不自动拥有 APPLICANT。

粗粒度角色门禁不代表 ownership。

真正：

```text
actor_id == approval.applicant_id
```

仍由 P1-002 状态机判定。

---

# 5. Request Validation

新增等价：

```text
WithdrawRequest
```

只允许：

```text
comment
```

comment：

- optional。
- None → None。
- str → strip。
- blank → None。
- max 2000 chars。
- non-string / overlong → APPROVAL_INVALID_REQUEST。

Pydantic structural validation（例如 extra forbidden field）沿用当前 FastAPI 行为。

本 TASK 不全局修改 RequestValidationError。

客户端禁止指定：

- status
- workflow_code
- workflow_version
- workflow_instance_id
- current_task_id
- current_node_code
- target_operator_id
- applicant_id

---

# 6. Workflow / Runtime Integrity

在合法 IN_APPROVAL withdraw 处理中必须验证：

1. ApprovalApplication 存在。
2. business_type == MASTER_DATA_CHANGE。
3. MasterDataChange 存在。
4. approval.business_id == str(change.id)。
5. workflow_code + workflow_version 完整。
6. workflow_instance_id 非空。
7. current_task_id 非空。
8. current_node_code 非空。
9. exact bound Workflow Definition 可读取。
10. current_node_code 存在于 exact bound workflow。
11. current_task_id 有对应的 task creation history，且属于当前 approval。

以上 IN_APPROVAL runtime 损坏：

```text
APPROVAL_DATA_INTEGRITY_ERROR
```

禁止 fallback active workflow。

---

# 7. State-vs-Runtime Error Ordering

P1-007 已修复过一个重要边界：

合法 DRAFT / COMPLETED 本来就没有 current task/node。

所以 P1-008 也必须避免：

```text
DRAFT + WITHDRAW
→ 因 current_task_id NULL
→ DATA_INTEGRITY 500
```

正确：

```text
DRAFT + WITHDRAW
→ P1-002 INVALID_STATUS_ACTION
→ APPROVAL_INVALID_TRANSITION
→ HTTP 409
```

以及：

```text
COMPLETED + WITHDRAW
→ APPROVAL_INVALID_TRANSITION
→ 409
```

但：

```text
IN_APPROVAL
+
runtime/binding missing
→ APPROVAL_DATA_INTEGRITY_ERROR
```

实现时可以复用或小幅扩展 P1-007 的 action-context precheck，但不能让 WITHDRAW_NOT_FIRST_NODE 掩盖真实 runtime corruption。

如果为了“状态优先”构造 synthetic TransitionContext：

- synthetic context 只能用于 status / applicant ownership precedence。
- 对 IN_APPROVAL 且 runtime 缺失，若 state machine 在 synthetic context 下可以通过，则 Service 必须继续报 DATA_INTEGRITY。
- 不得把缺 runtime 的 IN_APPROVAL 错报为 WITHDRAW_NOT_ALLOWED。

---

# 8. Exact Bound Workflow

WITHDRAW 判断 first node 必须使用：

```text
approval.workflow_code
approval.workflow_version
```

然后：

```text
WorkflowDefinitionRepository.get_by_code_version(...)
```

禁止：

```text
get_active(...)
```

原因：

审批申请已经绑定 immutable workflow snapshot。

如果 active v2 已切换，而当前审批仍绑定 v1：

```text
first-node 判定必须按 v1
```

---

# 9. First Node Semantics

合法撤回：

```text
spec.is_first_node(current_node.key) == True
```

不要求当前 task 是 role-pool。

也就是说：

如果审批曾：

```text
first node
→ approve
→ second node
→ reject previous
→ first node targeted-return task
```

此时当前 node 又是 first node。

按照 P1-002 当前冻结规则：

```text
申请人可以 WITHDRAW
```

因为规则是：

> 当前仍处于首个审批节点。

P1-008 不新增“历史上只要进入过后续节点就永久禁止撤回”的规则。

如果未来产品要该规则，必须单独改状态机和 TASK，不在本任务偷偷加入。

---

# 10. Later Node Semantics

如果当前：

```text
manager_review
→ 已 APPROVE
→ data_admin_review
```

当前 node 不是 first node。

申请人执行 WITHDRAW：

P1-002：

```text
WITHDRAW_NOT_FIRST_NODE
```

应用层映射：

```text
APPROVAL_WITHDRAW_NOT_ALLOWED
HTTP 409
```

这和：

```text
APPROVAL_INVALID_TRANSITION
```

区分：

- INVALID_TRANSITION = 当前 status 根本不允许 WITHDRAW。
- WITHDRAW_NOT_ALLOWED = 当前处于 IN_APPROVAL，但不满足 first-node 业务条件。

---

# 11. Actor / Ownership

申请人身份来自：

```text
AuthContext.user_id
```

ApprovalApplication.applicant_id 是可信持久化业务归属。

构造：

```python
TransitionContext(
    actor_id=str(auth_context.user_id),
    applicant_id=approval.applicant_id,
    is_first_approval_node=...,
)
```

P1-002：

```text
ACTOR_NOT_APPLICANT
```

映射：

```text
APPROVAL_FORBIDDEN
HTTP 403
```

禁止由 request body 传 applicant id。

---

# 12. Current Task History

虽然 WITHDRAW 不需要当前 approver assignment 做授权，但 current task 仍属于 workflow runtime 完整性的一部分。

Repository 应验证：

```text
target_task_id == approval.current_task_id
```

能找到创建当前 task 的 ApprovalRecord，并且：

```text
record.approval_id == approval.id
```

找不到：

```text
APPROVAL_DATA_INTEGRITY_ERROR
```

不需要读取或使用该 record 的 target_operator_id 来决定 withdraw 权限。

WITHDRAW 是 applicant-owned action，不是 current-assignee-owned action。

---

# 13. Successful Withdraw Runtime

成功后：

```text
status = DRAFT

workflow_code unchanged
workflow_version unchanged
workflow_instance_id unchanged

current_task_id = None
current_node_code = None
current_node_name = None

submitted_at unchanged
completed_at = None

ApprovalApplication.version unchanged
```

解释：

- workflow binding 保留，确保下一次 resubmit 继续 exact bound version。
- 当前 workflow_instance_id 代表上一轮提交 cycle，撤回后暂时保留。
- 下一次 P1-006 SUBMIT 会生成新的 workflow_instance_id 和 first task。
- submitted_at 保留“上一次成功提交时间”，下一次 submit 时再更新。
- completed_at 不存在。

---

# 14. WITHDRAW ApprovalRecord

成功 WITHDRAW append：

```text
action = WITHDRAW
operator_id = applicant actor

from_status = IN_APPROVAL
to_status = DRAFT

from_task_id = old current_task_id
from_node_code = old current_node_code
from_node_name = old current_node_name

target_task_id = None
target_node_code = None
target_node_name = None

target_operator_id = applicant_id

comment = normalized optional comment
```

为什么 target_operator_id = applicant_id：

撤回后的业务控制权回到申请人，但申请人不是 workflow approval task，所以不创建 target task/node。

ApprovalRecord 必须 append-only。

---

# 15. Response

复用 P1-007：

```text
ApprovalActionResponse
```

成功示例：

```json
{
  "approval_id": "uuid",
  "action": "WITHDRAW",
  "status": "DRAFT",
  "workflow_code": "supplier_change",
  "workflow_version": 1,
  "workflow_instance_id": "old-instance",
  "current_task_id": null,
  "current_node_code": null,
  "current_node_role": null,
  "completed_at": null
}
```

不要返回：

- target_operator_id
- workflow definition JSON
- internal assignment
- password/token
- internal stack

---

# 16. Error Mapping

扩展 ApprovalErrorCode：

```text
APPROVAL_WITHDRAW_NOT_ALLOWED
```

HTTP：

```text
409
```

映射：

```text
INVALID_STATUS_ACTION
→ APPROVAL_INVALID_TRANSITION

ACTOR_NOT_APPLICANT
→ APPROVAL_FORBIDDEN

WITHDRAW_NOT_FIRST_NODE
→ APPROVAL_WITHDRAW_NOT_ALLOWED

unexpected ApprovalTransitionError
→ APPROVAL_DATA_INTEGRITY_ERROR
```

继续复用：

```text
APPROVAL_NOT_FOUND → 404
APPROVAL_INVALID_REQUEST → 422
APPROVAL_DATA_INTEGRITY_ERROR → 500
```

所有 AppError 继续走 ErrorEnvelope + request_id。

401 保留：

```text
WWW-Authenticate: Bearer
```

---

# 17. Service Structure

继续扩展：

```text
ApprovalService
```

新增：

```python
withdraw(...)
```

允许对 P1-007 action context helper 做小幅、局部重构，以支持：

```text
APPROVE
REJECT
WITHDRAW
```

共同遵循：

- non-IN_APPROVAL status 先由 state machine 判 action invalid。
- IN_APPROVAL runtime corruption → DATA_INTEGRITY。

但禁止把 ApprovalService 重写成通用 workflow engine。

---

# 18. Transaction Boundary

```text
BEGIN

load approval + master data change
validate business linkage

preserve state/action precedence
validate exact bound workflow/runtime
resolve current node
verify current task creation history

build TransitionContext
decide_transition(WITHDRAW)

capture old runtime

status = DRAFT
clear current task/node/name
completed_at = None

append WITHDRAW record
flush

COMMIT
```

任何异常：

```text
ROLLBACK
```

Repository 不管理 commit / rollback。

---

# 19. Master Data Boundary

WITHDRAW 不修改：

```text
MasterDataEntity.data_json
MasterDataEntity.version

MasterDataChange.before_data
MasterDataChange.form_data
MasterDataChange.change_items
MasterDataChange.change_reason
MasterDataChange.base_entity_version
```

撤回只改变审批运行状态。

申请人后续修改仍通过：

```text
P1-005 MasterDataService SAVE
```

---

# 20. Approval Version Boundary

P1-008 不修改：

```text
ApprovalApplication.version
```

CAS / optimistic locking 统一留 P1-010。

禁止半实现 version increment。

---

# 21. No Migration

P1-008：

```text
NO DATABASE SCHEMA CHANGE
NO ALEMBIC MIGRATION
```

现有：

- ApprovalApplication
- ApprovalRecord

已经足够。

如果实现发现必须改 schema：

停止并提交 Architecture Deviation。

未经 Review 不得创建 migration。

---

# 22. Required Tests — API / Validation

1. unauthenticated withdraw → 401。
2. 401 includes WWW-Authenticate: Bearer。
3. 401/ErrorEnvelope includes request_id。
4. APPROVER-only → 403 AUTH_PERMISSION_DENIED。
5. DATA_ADMIN-only → 403 AUTH_PERMISSION_DENIED。
6. ADMIN-only → 403 AUTH_PERMISSION_DENIED。
7. APPLICANT role 可以进入 Service。
8. route role gate 不等于 ownership。
9. optional comment absent accepted。
10. blank comment normalized to None。
11. non-string comment → APPROVAL_INVALID_REQUEST 422。
12. overlong comment → APPROVAL_INVALID_REQUEST 422。
13. extra runtime/workflow fields rejected by structural validation。
14. successful response action == WITHDRAW。
15. successful response status == DRAFT。

---

# 23. Required Tests — State / Ownership

16. unknown approval → APPROVAL_NOT_FOUND。
17. DRAFT + WITHDRAW → APPROVAL_INVALID_TRANSITION。
18. COMPLETED + WITHDRAW → APPROVAL_INVALID_TRANSITION。
19. VOIDED + WITHDRAW → APPROVAL_INVALID_TRANSITION。
20. IN_APPROVAL + different applicant → APPROVAL_FORBIDDEN。
21. IN_APPROVAL + correct applicant + first node → success。
22. IN_APPROVAL + correct applicant + later node → APPROVAL_WITHDRAW_NOT_ALLOWED。
23. WITHDRAW uses P1-002 decide_transition。
24. no duplicated status/actor rule in Service。

---

# 24. Required Tests — Workflow / Runtime Integrity

25. wrong business type / linkage corruption → DATA_INTEGRITY。
26. missing MasterDataChange → DATA_INTEGRITY。
27. business_id mismatch → DATA_INTEGRITY。
28. partial workflow binding → DATA_INTEGRITY。
29. exact bound workflow missing → DATA_INTEGRITY。
30. corrupt bound workflow → DATA_INTEGRITY。
31. no fallback to active workflow。
32. missing workflow_instance_id while IN_APPROVAL → DATA_INTEGRITY。
33. missing current_task_id while IN_APPROVAL → DATA_INTEGRITY。
34. missing current_node_code while IN_APPROVAL → DATA_INTEGRITY。
35. unknown current_node_code → DATA_INTEGRITY。
36. current task creation history missing → DATA_INTEGRITY。
37. current task history belongs to another approval → DATA_INTEGRITY。
38. active v2 first node difference does not change bound v1 first-node decision。

---

# 25. Required Tests — Successful Withdraw

39. status becomes DRAFT。
40. workflow_code unchanged。
41. workflow_version unchanged。
42. workflow_instance_id unchanged。
43. current_task_id cleared。
44. current_node_code cleared。
45. current_node_name cleared。
46. submitted_at unchanged。
47. completed_at None。
48. ApprovalApplication.version unchanged。
49. MasterDataChange unchanged。
50. MasterDataEntity unchanged。
51. exactly one WITHDRAW record appended。
52. record operator == applicant。
53. from task/node/name equal old runtime。
54. target task/node/name null。
55. target_operator_id == applicant_id。
56. comment normalized/stored。

---

# 26. Required Tests — First Node / Targeted Return

57. normal first-node role-pool task can be withdrawn by applicant。
58. current approver assignment does not control withdraw permission。
59. targeted-return task at first node can still be withdrawn by applicant。
60. target_operator cannot withdraw unless also the applicant。
61. same request on second/later node returns WITHDRAW_NOT_ALLOWED。
62. no history-based permanent “曾进入后续节点禁止撤回” rule is introduced。

---

# 27. Required Tests — SAVE / RESUBMIT Chain

63. successful withdraw → real DRAFT with no current task/node。
64. applicant can SAVE through existing P1-005。
65. resubmit uses existing P1-006。
66. exact bound workflow version retained after withdraw。
67. active newer workflow does not replace old binding。
68. resubmit creates new workflow_instance_id。
69. resubmit creates new first task id。
70. resubmit current node = bound workflow first node。
71. resubmit updates submitted_at。
72. old WITHDRAW record preserved append-only。

---

# 28. Required Tests — Transaction / Rollback

73. inject failure after WITHDRAW ApprovalRecord flush。
74. whole transaction rolls back。
75. status remains IN_APPROVAL。
76. original current task/node/name restored。
77. submitted_at unchanged。
78. workflow binding/instance unchanged。
79. no WITHDRAW record remains。
80. MasterData unchanged。
81. version unchanged。

---

# 29. Regression

82. P1-001 tests green。
83. P1-002 tests green。
84. P1-003 tests green。
85. P1-004 tests green。
86. P1-005 tests green。
87. P1-006 tests green。
88. P1-007 tests green。
89. Ruff green。
90. format green。
91. Alembic no drift。
92. existing migration downgrade/re-upgrade green。
93. demo users seed green。
94. demo workflow seed green。
95. demo master data seed green。
96. frontend lint/typecheck/test/build green。

---

# 30. Suggested Files

预计修改：

```text
backend/app/approval/errors.py
backend/app/approval/schemas.py
backend/app/approval/service.py
backend/app/api/errors.py
backend/app/api/v1/approvals.py
backend/tests/test_approval_withdraw.py
```

可能无需修改 Repository，因为：

```text
get_application_for_action(...)
get_task_creation_record(...)
save_transition(...)
```

已经足够。

如果确需 Repository 调整，只允许小范围查询复用。

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

若必须修改冻结文件：

Pre-Implementation Plan 必须解释原因。

---

# 31. Frozen Decisions

1. P1-008 只实现 WITHDRAW。
2. Withdraw route = APPLICANT coarse gate。
3. ownership 由 P1-002 actor/applicant 规则判断。
4. only current first node can withdraw。
5. later node → APPROVAL_WITHDRAW_NOT_ALLOWED 409。
6. DRAFT/COMPLETED/VOIDED → APPROVAL_INVALID_TRANSITION 409。
7. exact bound workflow only。
8. never fallback active workflow。
9. normal first-node task can withdraw。
10. targeted-return first-node task also can withdraw。
11. no historical “once progressed, never withdraw” rule。
12. current task history must exist for IN_APPROVAL runtime integrity。
13. current task target_operator does not authorize WITHDRAW。
14. success → DRAFT。
15. workflow_code/version retained。
16. workflow_instance_id retained until resubmit。
17. submitted_at retained until resubmit。
18. current task/node/name cleared。
19. completed_at None。
20. ApprovalApplication.version unchanged。
21. target_operator_id on WITHDRAW record = applicant_id。
22. no MasterData writeback。
23. comment optional max 2000。
24. no schema change。
25. no migration。
26. no optimistic lock / idempotency。
27. no P1-009。

任何冻结项需要改变：

停止并提交 Architecture Deviation。

---

# 32. Definition of Done

- [ ] withdraw API
- [ ] WithdrawRequest
- [ ] optional comment validation
- [ ] APPLICANT role gate
- [ ] applicant ownership via state machine
- [ ] exact bound workflow lookup
- [ ] first-node determination
- [ ] later-node stable error
- [ ] state-before-runtime error ordering
- [ ] current task history integrity
- [ ] targeted first-node semantics
- [ ] DRAFT runtime clearing
- [ ] WITHDRAW ApprovalRecord
- [ ] transaction rollback
- [ ] MasterData immutability
- [ ] Approval version unchanged
- [ ] SAVE after withdraw
- [ ] resubmit after withdraw
- [ ] exact workflow version after resubmit
- [ ] new workflow instance after resubmit
- [ ] no migration
- [ ] full PostgreSQL tests
- [ ] P1-001～P1-007 regression
- [ ] backend CI
- [ ] frontend CI
- [ ] no P1-009

---

# 33. Completion Report

Codex 最终必须输出：

```text
TASK Completion Report

TASK ID:
TASK-P1-008

Status:
READY_FOR_REVIEW

Changed files:

Withdraw API:

Request validation:

Role gate:

Applicant ownership:

State-machine reuse:

State-vs-runtime ordering:

Bound workflow lookup:

First-node rule:

Later-node behavior:

Targeted-return first-node behavior:

Current task history integrity:

Successful runtime update:

WITHDRAW ApprovalRecord:

Comment behavior:

Transaction rollback:

MasterData immutability:

Approval version behavior:

Withdraw → SAVE → resubmit:

Exact workflow version after resubmit:

Workflow instance behavior:

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

# 34. Codex Pre-Implementation Plan Prompt

```text
当前进入 TASK-P1-008 — Withdraw。

本轮不要编码。

Source of Truth：

- CODEX.md
- docs/V2.1-technical-design.md
- docs/V3.0-codex-execution-manual.md
- docs/tasks/TASK-P1-008.md

P1-008 具体范围以：

docs/tasks/TASK-P1-008.md

为最高优先级。

开始前执行：

git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short

预期：

branch:
task/p1-008

HEAD 必须包含本任务文档提交，并基于：

main:
f6112c3f94afef9255f75d4ff8e4c28cd7f0514b

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
- docs/tasks/TASK-P1-008.md
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

重点阅读 P1-007：

- ApprovalService._load_action_context
- ApprovalService._decide
- P1-007 的 real DRAFT / COMPLETED invalid-transition 修复

本轮只输出：

# Pre-Implementation Plan — TASK-P1-008

至少覆盖：

1. TASK 目标和边界
2. 新增 / 修改文件
3. 为什么不需要 migration
4. withdraw API
5. WithdrawRequest / comment validation
6. APPLICANT route coarse gate
7. AuthContext ownership
8. P1-002 WITHDRAW state machine reuse
9. INVALID_STATUS_ACTION mapping
10. ACTOR_NOT_APPLICANT mapping
11. WITHDRAW_NOT_FIRST_NODE mapping
12. DRAFT/COMPLETED 无 runtime 时如何保证 INVALID_TRANSITION
13. IN_APPROVAL 缺 runtime 时如何保证 DATA_INTEGRITY
14. 为什么不能直接照搬 P1-007 synthetic context
15. exact bound workflow lookup
16. no fallback active workflow
17. first-node determination
18. later-node rejection
19. targeted-return first-node why still withdrawable
20. current task creation history integrity
21. 为什么 withdraw 不使用 target_operator 做授权
22. success 后 runtime 字段
23. workflow binding semantics
24. workflow_instance_id semantics
25. submitted_at semantics
26. completed_at semantics
27. ApprovalApplication.version semantics
28. WITHDRAW ApprovalRecord from/target fields
29. target_operator_id 为什么是 applicant
30. transaction boundary
31. rollback behavior
32. MasterData immutability
33. withdraw → SAVE → resubmit chain
34. exact workflow version on resubmit
35. new workflow instance on resubmit
36. PostgreSQL integration tests
37. API tests
38. regression tests
39. CI 是否需要变化
40. concurrency limitation
41. Architecture risks / reviewer questions
42. Out of Scope
43. 如何保证不进入 P1-009

不要编码。
不要生成 migration。
不要修改文件。
不要安装依赖。

等待 Architecture Review APPROVE。
```
