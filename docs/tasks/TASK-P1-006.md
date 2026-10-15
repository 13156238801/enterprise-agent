# TASK-P1-006 — Submit Transaction

## 0. Status

```text
TASK ID: P1-006
Name: Submit Transaction
Phase: Phase 1 — Approval Backend
Status: DESIGN FROZEN / WAITING FOR PRE-IMPLEMENTATION PLAN
```

本文件是 TASK-P1-006 的具体执行 Source of Truth。

如本文件与 V2.1 / V3.0 中历史示例存在差异，以本文件和已经合并的 P1-001～P1-005 实现为准。

开始基线：

```text
main:
b0c2e3614bf892388548d5b2dcfd1997af43cb71

P1-005:
MasterDataEntity + Change Draft / Diff
```

P1-006 不重新设计 P1-001～P1-005。

---

# 1. Goal

把一个已经存在的主数据 DRAFT 审批草稿真正提交进入审批流：

```text
ApprovalApplication(DRAFT)
        +
MasterDataChange
        ↓
验证申请人 / 状态
        ↓
解析该业务应使用的 Workflow code
        ↓
首次提交：选择 active Workflow Definition
再次提交：复用已绑定的 exact Workflow version
        ↓
绑定 workflow code/version
        ↓
初始化 workflow instance / first task / first node
        ↓
DRAFT → IN_APPROVAL
        ↓
写 SUBMIT ApprovalRecord
        ↓
commit
```

本 TASK 的核心是：

- Submit transaction。
- Workflow version binding。
- 首审批节点 runtime 初始化。
- P1-002 SUBMIT 状态机复用。
- SUBMIT history。
- REST submit endpoint。
- PostgreSQL transaction / migration / regression tests。

---

# 2. Explicit Out of Scope

P1-006 禁止实现：

- approve
- reject
- withdraw
- list approvals
- approval detail/history API
- concrete assignee allocation
- pending-task query
- final approval writeback to MasterDataEntity
- optimistic-lock enforcement
- approval.version CAS
- Idempotency-Key persistence/semantics
- audit_logs
- HITL confirmation token issuance/verification
- Agent
- Tool Executor
- LLM
- Redis lock
- workflow designer
- BPMN
- P1-007 或后续任务

其中：

```text
Idempotency-Key
Optimistic Lock
Audit
```

统一留给 TASK-P1-010。

结构化 HITL / confirmation token 由后续 Tool / Agent 阶段实现。

P1-006 不伪造一个“看起来验证了、实际无法验证”的 confirmation_token。

---

# 3. Current State Baseline

P1-005 已有：

```text
ApprovalApplication
- id
- business_type
- business_id
- applicant_id
- status
- workflow_instance_id
- current_task_id
- current_node_code
- current_node_name
- version
- submitted_at
- completed_at

MasterDataChange
- approval_id
- entity_type
- entity_key
- before_data
- form_data
- change_items
- base_entity_version
...
```

P1-004 已有 immutable Workflow Definition：

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

P1-002 已有：

```text
DRAFT + SUBMIT + actor == applicant
→ IN_APPROVAL
```

P1-006 必须复用这些能力，不复制第二套状态规则。

---

# 4. Workflow Binding Model

P1-006 在 `approval_application` 新增：

| Field | Type | Rule |
|---|---|---|
| workflow_code | VARCHAR(128) | nullable |
| workflow_version | INTEGER | nullable |

DRAFT 首次提交前：

```text
workflow_code = null
workflow_version = null
```

首次成功提交后：

```text
workflow_code = supplier_change
workflow_version = active version
```

后续即使申请回到 DRAFT：

```text
workflow_code / workflow_version 保留
```

再次提交时必须复用 exact bound version，不重新选择 active version。

---

# 5. Why Store code + version Instead of Copying definition_json

P1-004 已冻结：

- Workflow Definition version immutable。
- historical versions retained。
- `(code, version)` unique。
- JSON payload 与 code/version 必须一致。

因此 ApprovalApplication 保存：

```text
workflow_code
workflow_version
```

即可形成稳定绑定。

P1-006 不复制 definition_json 到 ApprovalApplication。

---

# 6. Database Constraints for Workflow Binding

新增：

```text
CHECK:
workflow_code/workflow_version 要么同时 NULL，要么同时 NOT NULL

CHECK:
workflow_version IS NULL OR workflow_version > 0

FOREIGN KEY:
(workflow_code, workflow_version)
→ workflow_definitions(code, version)
```

建议名称：

```text
ck_approval_application_workflow_binding_pair
ck_approval_application_workflow_version_positive
fk_approval_application_workflow_definition
```

Nullable composite FK 允许尚未提交的 DRAFT 不绑定流程。

---

# 7. Workflow Selection Rule

P1-006 不允许 request body / LLM / Tool 参数直接指定可信 workflow code。

由服务端业务规则解析。

当前冻结 Demo routing：

```text
BusinessType.MASTER_DATA_CHANGE
+
MasterDataChange.entity_type == "supplier"
→ workflow code = "supplier_change"
```

不要使用：

```text
client supplied workflow_code
```

也不要自动对任意 entity_type 使用：

```text
f"{entity_type}_change"
```

一期只支持显式已批准的 supplier mapping。

未来扩展其他 master data type 时再增加显式 mapping。

---

# 8. First Submit vs Resubmit

## 8.1 First Submit

如果：

```text
workflow_code is NULL
workflow_version is NULL
```

则：

1. resolve workflow code = `supplier_change`
2. `WorkflowDefinitionRepository.get_active(code)`
3. 如果不存在 active version → 提交失败
4. 将 exact code/version 写入 ApprovalApplication

## 8.2 Resubmit

如果：

```text
workflow_code + workflow_version 已存在
```

则：

1. 不读取 active version 来替换绑定。
2. `get_by_code_version(bound_code, bound_version)`
3. 必须复用 exact version。
4. 后续 Workflow 配置变化不能改变该 ApprovalApplication 的运行定义。

## 8.3 Partial Binding

以下非法：

```text
workflow_code != null
workflow_version == null

or

workflow_code == null
workflow_version != null
```

视为 approval data integrity error。

---

# 9. Workflow Runtime Initialization

每次成功 SUBMIT，包括未来的 resubmit，都生成新的：

```text
workflow_instance_id = str(uuid4())
current_task_id = str(uuid4())
```

并：

```text
current_node_code = workflow.first_node().key
```

P1-004 node 只有：

```text
key
role
```

没有 display name。

因此 P1-006：

```text
current_node_name = None
```

禁止把 node key 假装成 display name。

---

# 10. Concrete Assignee Boundary

P1-004 已冻结：

```text
node.role = qualification
不是 concrete assignee
```

P1-006 不解析：

```text
current_assignee_ids
target_operator_id
```

因此：

```text
ApprovalRecord.target_operator_id = None
```

P1-007 将基于：

```text
bound workflow version
+
current_node_code
+
trusted AuthContext.roles
```

完成当前节点审批权限判断。

P1-006 不提前实现 P1-007。

---

# 11. Submit State Machine

必须调用 P1-002：

```python
decision = decide_transition(
    current_status=approval.status,
    action=ApprovalAction.SUBMIT,
    context=TransitionContext(
        actor_id=str(auth_context.user_id),
        applicant_id=approval.applicant_id,
    ),
)
```

禁止在 Service 中复制：

```python
if approval.status != DRAFT:
    ...

if actor != applicant:
    ...
```

作为 SUBMIT 的主业务规则。

状态机是：

```text
status + actor
```

的唯一事实来源。

---

# 12. Submit Transaction Order

推荐固定顺序：

```text
BEGIN
  ↓
load ApprovalApplication + MasterDataChange
  ↓
validate application/business linkage
  ↓
decide_transition(SUBMIT)
  ↓
resolve workflow code
  ↓
first submit:
  get active workflow
or resubmit:
  get exact bound workflow
  ↓
capture old runtime context for history
  ↓
generate new workflow_instance_id / task_id
  ↓
set workflow binding if first submit
  ↓
set status = decision.to_status
  ↓
set current runtime to first node
  ↓
set submitted_at = now(UTC)
  ↓
append SUBMIT ApprovalRecord
  ↓
flush
COMMIT
```

任一步失败：

```text
ROLLBACK
```

---

# 13. Application / Business Integrity Checks

Submit Service 自己负责检查：

1. ApprovalApplication 存在。
2. `business_type == MASTER_DATA_CHANGE`。
3. 对应 `MasterDataChange` 存在。
4. `approval.business_id == str(change.id)`。
5. workflow binding pair 完整。
6. 已绑定 exact workflow version 必须仍可读取。

这些不是 P1-002 状态规则，因此由 Service / Repository 边界负责。

P1-006 不修改 MasterDataChange 内容。

---

# 14. SUBMIT ApprovalRecord

成功提交必须 append：

```text
action = SUBMIT
operator_id = current AuthContext.user_id
from_status = decision.from_status
to_status = decision.to_status
```

并记录 runtime target：

```text
target_task_id = newly generated current_task_id
target_node_code = first_node.key
target_node_name = None
target_operator_id = None
```

from runtime 字段使用提交前 ApprovalApplication 上已有值：

```text
from_task_id
from_node_code
from_node_name
```

首次提交通常为 NULL。

ApprovalRecord append-only。

不得更新已有 record。

---

# 15. ApprovalApplication Fields After Successful Submit

成功后必须满足：

```text
status = IN_APPROVAL

workflow_code = bound code
workflow_version = bound version

workflow_instance_id != null
current_task_id != null
current_node_code = first workflow node key
current_node_name = null

submitted_at = current UTC time
completed_at = null
```

P1-006 不修改：

```text
ApprovalApplication.version
```

原因：

完整的 version increment + compare-and-swap 语义统一留给 P1-010，不能只在 submit 路径半实现。

---

# 16. submitted_at Semantics

`submitted_at` 表示：

```text
该 ApprovalApplication 最近一次成功 SUBMIT 的时间
```

因此未来 resubmit：

```text
submitted_at 更新为新的 UTC 时间
```

每次 SUBMIT 的历史事实仍由 ApprovalRecord 保留。

---

# 17. Master Data Boundary

P1-006 提交审批：

- 不修改 `MasterDataEntity.data_json`。
- 不修改 `MasterDataEntity.version`。
- 不修改 `MasterDataChange.before_data`。
- 不修改 `MasterDataChange.form_data`。
- 不修改 `MasterDataChange.change_items`。
- 不修改 `base_entity_version`。

P1-006 也不比较：

```text
MasterDataEntity.version
vs
MasterDataChange.base_entity_version
```

stale draft / optimistic-lock enforcement 留给 P1-010。

---

# 18. Approval Package

新增建议：

```text
backend/app/approval/
├── __init__.py
├── errors.py
├── repository.py
├── schemas.py
└── service.py
```

不要把 submit transaction 塞进 `MasterDataService`。

原因：

从 P1-006 开始，submit / approve / reject / withdraw 属于 Approval Application 生命周期，而不是 Master Data Draft 编辑职责。

---

# 19. ApprovalRepository

至少提供：

```text
get_application_for_action(approval_id)
save_transition(record)
```

命名可按现有风格等价调整。

要求：

- injected AsyncSession。
- 不 commit。
- 不 rollback。
- 不创建 AuthContext。
- 不映射 HTTP error。
- 不依赖 FastAPI。
- 必须能加载 MasterDataChange，避免 Async lazy-load。

Workflow Definition 继续使用已经存在的：

```text
WorkflowDefinitionRepository
```

不要复制一套 Workflow query。

---

# 20. ApprovalService

至少：

```text
submit(...)
```

负责：

- transaction boundary。
- trusted identity。
- business linkage。
- P1-002 SUBMIT transition。
- workflow routing。
- workflow version binding。
- runtime IDs。
- SUBMIT history。
- response。

不负责：

- idempotency。
- confirmation token。
- assignee allocation。
- optimistic locking。
- approve/reject/withdraw。

---

# 21. REST API

新增：

```http
POST /api/v1/approvals/{approval_id}/submit
Authorization: Bearer <token>
```

P1-006 暂不要求 request body。

原因：

- confirmation token 尚无合法 issuer / verifier。
- Idempotency-Key 语义冻结在 P1-010。
- 不允许做安全语义为空的占位验证。

最终 HIGH-risk Agent Tool 的 HITL 将在后续 Phase 接入。

---

# 22. Submit Response

建议：

```json
{
  "approval_id": "uuid",
  "status": "IN_APPROVAL",
  "workflow_code": "supplier_change",
  "workflow_version": 1,
  "workflow_instance_id": "uuid-string",
  "current_task_id": "uuid-string",
  "current_node_code": "manager_review",
  "current_node_role": "APPROVER",
  "submitted_at": "2026-10-15T..."
}
```

不返回：

- password/token/hash。
- concrete assignee。
- workflow definition 完整 JSON。
- internal stack/debug 信息。

---

# 23. Auth / Permission

Route：

```python
require_any_role(UserRole.APPLICANT)
```

只允许明确拥有 APPLICANT 角色的认证用户进入 submit service。

但：

```text
APPLICANT role
!=
可以提交任意人的审批
```

具体 ownership 仍由 P1-002：

```text
actor_id == applicant_id
```

判断。

ADMIN 不隐式拥有 APPLICANT。

---

# 24. Error Contract

新增 Approval errors。

至少：

| Code | HTTP | Meaning |
|---|---:|---|
| APPROVAL_NOT_FOUND | 404 | approval 不存在 |
| APPROVAL_INVALID_TRANSITION | 409 | 当前状态不能 submit |
| APPROVAL_FORBIDDEN | 403 | 不是原申请人 |
| APPROVAL_WORKFLOW_NOT_CONFIGURED | 409 | 首次提交没有 active workflow |
| APPROVAL_DATA_INTEGRITY_ERROR | 500 | 业务关联 / workflow binding / definition 数据不完整 |

要求：

- 使用现有 ErrorEnvelope。
- request_id 保留。
- 401 Bearer 行为不破坏。
- domain/repository 不抛 HTTPException。

状态机映射：

```text
INVALID_STATUS_ACTION
→ APPROVAL_INVALID_TRANSITION

ACTOR_NOT_APPLICANT
→ APPROVAL_FORBIDDEN

其他非预期 ApprovalTransitionError
→ APPROVAL_DATA_INTEGRITY_ERROR
```

WorkflowDefinitionError：

```text
→ APPROVAL_DATA_INTEGRITY_ERROR
```

---

# 25. Sequential Duplicate Submit

P1-006 不实现真正 Idempotency-Key。

因此同一个 approval：

第一次：

```text
DRAFT → IN_APPROVAL
成功
```

第二次顺序调用：

```text
IN_APPROVAL + SUBMIT
→ APPROVAL_INVALID_TRANSITION
```

必须保证：

- 不产生第二条 SUBMIT record。
- 不生成并保存第二套 runtime context。

并发双提交的完整保证留给 P1-010。

---

# 26. Bound Workflow Must Survive Active Version Changes

需要覆盖：

```text
first submit:
supplier_change v1 active
→ bind v1

later:
v2 becomes active

application returns to DRAFT in future
→ resubmit

must still use v1
```

P1-006 可以通过 service/integration test 模拟 DRAFT resubmit。

这是 Workflow version binding 的核心验收。

---

# 27. Runtime Identity Rules

```text
workflow_instance_id
current_task_id
```

均由服务端：

```text
str(uuid4())
```

生成。

不得来自：

- request。
- LLM。
- frontend。
- Tool args。

每次成功 SUBMIT 生成新的 runtime IDs。

Workflow code/version 不由 UUID 决定。

---

# 28. Migration

新增 P1-006 migration。

父 revision 必须：

```text
c5a005d1f005
```

只修改：

```text
approval_application
```

新增：

```text
workflow_code
workflow_version
```

以及：

- pair CHECK。
- positive version CHECK。
- composite FK to workflow_definitions(code, version)。

已有 DRAFT 行保持：

```text
workflow_code = null
workflow_version = null
```

不做虚假 backfill。

---

# 29. Migration Downgrade

Downgrade 到：

```text
c5a005d1f005
```

必须只移除 P1-006 新增：

- composite FK。
- workflow checks。
- workflow_version。
- workflow_code。

并保留：

```text
P1-001～P1-005 所有表与字段
master_data_entities
MasterDataChange.before_data
MasterDataChange.base_entity_version
workflow_definitions
auth tables
approval tables
```

需要 PostgreSQL smoke。

---

# 30. Required Tests — ORM / Migration

1. workflow_code nullable。
2. workflow_version nullable。
3. binding pair CHECK。
4. positive workflow_version CHECK。
5. composite FK exists。
6. unbound DRAFT row合法。
7. bound exact existing definition合法。
8. non-existing workflow code/version 被 FK 拒绝。
9. partial binding 被 CHECK 拒绝。
10. workflow_version <= 0 被 CHECK 拒绝。
11. migration parent = c5a005d1f005。
12. upgrade head。
13. alembic check no drift。
14. downgrade to c5a005d1f005。
15. P1-005 schema preserved。
16. re-upgrade head。

---

# 31. Required Tests — Submit Service

17. unknown approval → APPROVAL_NOT_FOUND。
18. linked MasterDataChange missing → DATA_INTEGRITY。
19. business_id/change.id mismatch → DATA_INTEGRITY。
20. non-DRAFT → INVALID_TRANSITION。
21. different APPLICANT → FORBIDDEN。
22. SUBMIT uses P1-002 state machine。
23. first submit selects active workflow。
24. no active workflow → WORKFLOW_NOT_CONFIGURED。
25. first submit binds workflow code/version。
26. status becomes IN_APPROVAL。
27. submitted_at set UTC-aware。
28. workflow_instance_id generated。
29. current_task_id generated。
30. current_node_code = first node。
31. current_node_name remains None。
32. SUBMIT record written。
33. record from/to statuses from TransitionDecision。
34. record target_task_id matches application.current_task_id。
35. record target_node_code matches first node。
36. target_operator_id is None。
37. ApprovalRecord append-only。
38. MasterDataChange snapshots unchanged。
39. MasterDataEntity unchanged。
40. ApprovalApplication.version unchanged。
41. transaction flush 后异常整体 rollback。

---

# 32. Required Tests — Workflow Binding

42. active v1 first submit binds v1。
43. active version later becomes v2。
44. bound approval resubmit still loads v1。
45. resubmit does not rewrite workflow binding。
46. resubmit creates new workflow_instance_id。
47. resubmit creates new current_task_id。
48. partial binding → DATA_INTEGRITY。
49. bound exact version missing/corrupt → DATA_INTEGRITY。
50. unsupported business/entity mapping → WORKFLOW_NOT_CONFIGURED 或已冻结等价稳定错误，不得自动猜 workflow。

---

# 33. Required Tests — API

51. unauthenticated submit → 401 + Bearer。
52. APPROVER-only → 403。
53. DATA_ADMIN-only → 403。
54. ADMIN-only → 403。
55. APPLICANT own draft → 200。
56. APPLICANT other user's draft → 403 APPROVAL_FORBIDDEN。
57. unknown approval → 404。
58. invalid transition → 409。
59. missing active workflow → 409。
60. ErrorEnvelope includes request_id。
61. response exposes workflow/node runtime fields only。
62. request cannot inject workflow_code/version/runtime IDs。

---

# 34. Required Tests — Duplicate / Rollback

63. sequential duplicate submit second call → 409。
64. only one SUBMIT record remains。
65. second call does not replace current runtime IDs。
66. post-flush injected failure restores DRAFT status。
67. rollback removes SUBMIT record。
68. rollback restores workflow binding/runtime fields/submitted_at。
69. no partial workflow binding survives rollback。

---

# 35. Regression

70. P1-001 tests green。
71. P1-002 tests green。
72. P1-003 tests green。
73. P1-004 tests green。
74. P1-005 tests green。
75. backend CI green。
76. frontend CI green。
77. existing user/workflow/master-data seeds green。

---

# 36. CI Changes

Backend CI 保留现有全部 Gate。

增加：

```text
upgrade head
alembic check

downgrade c5a005d1f005
verify P1-006 downgrade preserves P1-005
re-upgrade head
alembic check

继续执行 P1-005 downgrade/backfill/re-upgrade
继续执行 P1-004 downgrade/re-upgrade

pytest
seed_demo_users
seed_demo_workflows
seed_demo_master_data
```

新增脚本建议：

```text
backend/scripts/verify_p1_006_downgrade.py
```

只验证 schema，不 seed 业务数据。

---

# 37. Suggested Structure

允许按现有代码风格等价调整：

```text
backend/app/
├── approval/
│   ├── __init__.py
│   ├── errors.py
│   ├── repository.py
│   ├── schemas.py
│   └── service.py
├── api/
│   └── v1/
│       └── approvals.py
└── models/
    └── approval.py

backend/alembic/versions/
└── <revision>_bind_workflow_on_submit.py

backend/scripts/
└── verify_p1_006_downgrade.py

backend/tests/approval/
├── test_submit_service.py
└── test_submit_api.py
```

---

# 38. Architecture Boundaries

必须保持：

```text
Domain State Machine
= transition legality

Approval Service
= transaction orchestration + workflow binding

Workflow Repository
= immutable definition lookup

AuthContext
= trusted actor identity / roles

Database
= relationship constraints + atomic persistence
```

不得让：

- API route 直接修改 ORM 状态。
- Workflow Repository commit。
- LLM 选 workflow。
- request 指定 applicant identity。
- request 指定 current node。
- request 指定 runtime ID。

---

# 39. Comments / Docstrings

继续项目规则：

- 所有函数 / 方法中文 docstring。
- ORM / dataclass / Enum / error class 中文 docstring。
- test function 一行中文 docstring。
- 复杂逻辑注释解释 why。

重点解释：

1. 为什么首次提交选 active，resubmit 复用 bound version。
2. 为什么 workflow binding 用 code/version。
3. 为什么不把 node key 当 display name。
4. 为什么不解析 concrete assignee。
5. 为什么 P1-006 不做 optimistic lock/idempotency。
6. 为什么 runtime IDs 服务端生成。
7. 为什么 MasterData snapshot 在 submit 中不可变。

---

# 40. Frozen Decisions

1. P1-006 = Submit Transaction。
2. 新增 workflow_code/workflow_version。
3. 两字段 pair nullable。
4. composite FK 到 immutable workflow version。
5. 首次 submit 选择 active definition。
6. resubmit 复用 bound exact version。
7. supplier master data 显式映射 supplier_change。
8. 不接受 client workflow code。
9. SUBMIT 复用 P1-002。
10. APPLICANT role guard + state machine ownership。
11. 每次成功 submit 生成新的 workflow_instance_id。
12. 每次成功 submit 生成新的 current_task_id。
13. current node = first workflow node。
14. current_node_name = None。
15. 不解析 concrete assignee。
16. target_operator_id = None。
17. SUBMIT record append-only。
18. submitted_at = latest successful submit time。
19. MasterDataEntity/Change 不被 submit 修改。
20. 不检查 base_entity_version。
21. 不改 ApprovalApplication.version。
22. 不实现 Idempotency-Key。
23. 不实现 confirmation_token。
24. sequential duplicate submit 返回 invalid transition。
25. concurrent idempotency 留 P1-010。
26. migration parent = c5a005d1f005。
27. 不进入 P1-007。

任何冻结项需改变：

停止并提交 Architecture Deviation，不得自行调整。

---

# 41. Definition of Done

- [ ] workflow binding columns
- [ ] pair / positive CHECK
- [ ] composite workflow FK
- [ ] migration / downgrade
- [ ] Approval package
- [ ] ApprovalRepository
- [ ] ApprovalService.submit
- [ ] P1-002 SUBMIT reuse
- [ ] explicit supplier_change routing
- [ ] first submit active workflow lookup
- [ ] exact bound version reuse
- [ ] workflow runtime IDs
- [ ] first node initialization
- [ ] SUBMIT ApprovalRecord
- [ ] submitted_at
- [ ] current_node_name stays null
- [ ] no concrete assignee
- [ ] POST submit API
- [ ] APPLICANT role guard
- [ ] Approval ErrorEnvelope
- [ ] sequential duplicate behavior
- [ ] transaction rollback
- [ ] workflow binding persistence tests
- [ ] resubmit keeps old version
- [ ] P1-005 snapshot unchanged
- [ ] P1-001～P1-005 regression
- [ ] backend CI
- [ ] frontend CI
- [ ] no P1-007 implementation

---

# 42. Completion Report

Codex 最终必须输出：

```text
TASK Completion Report

TASK ID:
TASK-P1-006

Status:
READY_FOR_REVIEW

Changed files:

ApprovalApplication schema evolution:

Workflow binding:

Workflow routing:

First submit behavior:

Resubmit behavior:

P1-002 SUBMIT reuse:

Runtime initialization:

SUBMIT ApprovalRecord:

Auth / permission:

REST API:

Error mapping:

Sequential duplicate behavior:

MasterData immutability:

Approval version behavior:

Migration:

Migration parent:

Upgrade verification:

Downgrade verification:

Schema drift:

PostgreSQL integration tests:

Transaction rollback tests:

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

# 43. Codex Pre-Implementation Plan Prompt

```text
当前进入 TASK-P1-006 — Submit Transaction。

本轮不要编码。

Source of Truth：

- CODEX.md
- docs/V2.1-technical-design.md
- docs/V3.0-codex-execution-manual.md
- docs/tasks/TASK-P1-006.md

P1-006 具体范围以：

docs/tasks/TASK-P1-006.md

为最高优先级。

开始前执行：

git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short

预期：

branch:
task/p1-006

HEAD 必须包含本任务文档提交，并基于已合并 P1-005 main：

b0c2e3614bf892388548d5b2dcfd1997af43cb71

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
- docs/tasks/TASK-P1-006.md
- backend/app/domain/approval/**
- backend/app/domain/workflow/**
- backend/app/models/**
- backend/app/auth/**
- backend/app/master_data/**
- backend/app/workflow/**
- backend/app/api/**
- backend/alembic/**
- backend/scripts/**
- backend/tests/**
- .github/workflows/ci.yml

本轮只输出：

# Pre-Implementation Plan — TASK-P1-006

至少覆盖：

1. TASK 目标与边界
2. 新增 / 修改文件
3. ApprovalApplication workflow_code/workflow_version
4. pair CHECK
5. positive version CHECK
6. composite FK
7. migration parent
8. first submit workflow binding
9. resubmit exact-version reuse
10. explicit supplier -> supplier_change mapping
11. 为什么不接受 client workflow code
12. WorkflowDefinitionRepository 复用
13. workflow binding integrity validation
14. P1-002 SUBMIT state machine reuse
15. ownership rule
16. APPLICANT route guard
17. workflow_instance_id generation
18. current_task_id generation
19. first node initialization
20. current_node_name 为什么为 None
21. concrete assignee 为什么不在本 TASK 解析
22. SUBMIT ApprovalRecord from/target fields
23. submitted_at semantics
24. ApprovalApplication.version 为什么不改
25. MasterDataEntity/Change immutability
26. base_entity_version 为什么不检查
27. ApprovalRepository
28. ApprovalService transaction boundary
29. REST endpoint
30. request body 当前为什么为空
31. Idempotency-Key 为什么留 P1-010
32. confirmation token 为什么不做占位假验证
33. Approval error codes
34. ErrorEnvelope mapping
35. sequential duplicate submit
36. concurrent duplicate limitation
37. resubmit after active workflow version changes
38. post-flush rollback
39. migration downgrade
40. PostgreSQL constraint tests
41. service integration tests
42. API tests
43. regression tests
44. CI changes
45. 如何保证不进入 P1-007
46. Architecture risks / reviewer questions
47. Out of Scope

不要编码。
不要生成 migration。
不要修改文件。
不要安装依赖。

等待 Architecture Review APPROVE。
```
