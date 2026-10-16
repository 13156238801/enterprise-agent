# TASK-P1-009 — Approval List / Pending / Detail / History APIs

## 0. Status

```text
TASK ID: P1-009
Name: Approval List / Pending / Detail / History APIs
Phase: Phase 1 — Approval Backend
Status: DESIGN FROZEN / WAITING FOR PRE-IMPLEMENTATION PLAN
```

本文件是 TASK-P1-009 的具体执行 Source of Truth。

如本文件与 V2.1 / V3.0 中历史示例存在差异，以本文件和已经合并的 P1-001～P1-008 实现为准。

开始基线：

```text
main:
35a257f522708fb6a5e7b048fc257912cf2ddf8e

P1-008:
Withdraw
```

P1-009 是 Phase 1 的只读查询任务，不重新设计 P1-001～P1-008 的写操作、状态机或授权模型。

---

# 1. Goal

实现 Approval Backend 的四类只读查询能力：

```text
GET /api/v1/approvals
→ 我发起的审批

GET /api/v1/approvals/pending
→ 待我审批

GET /api/v1/approvals/{approval_id}
→ 审批详情

GET /api/v1/approvals/{approval_id}/history
→ 审批历史
```

本 TASK 核心：

- 服务端身份驱动的数据权限。
- applicant 自己的审批列表。
- reviewer 当前待办列表。
- detail/history 的最小权限模型。
- exact bound workflow 解析当前节点角色。
- 复用 P1-007 的 role-pool / targeted-task 语义。
- 主数据变更 Diff / Snapshot 的结构化详情输出。
- append-only ApprovalRecord 的安全历史视图。
- 稳定排序与简单分页。
- PostgreSQL integration tests。

---

# 2. Explicit Out of Scope

P1-009 禁止实现：

- submit / approve / reject / withdraw 新规则
- optimistic lock / CAS
- Idempotency-Key
- audit_logs
- confirmation token
- Agent / Tool / LLM / MCP
- Redis cache
- SSE
- frontend approval pages
- full-text search
- arbitrary admin data browser
- organization / department hierarchy
- dynamic approver assignment
- export / CSV
- workflow editor
- P1-010

不得为了查询接口重写 Approval State Machine 或 ApprovalService 写操作。

---

# 3. Source Alignment

V2.1 已定义：

```text
GET /api/v1/approvals
GET /api/v1/approvals/pending
GET /api/v1/approvals/{id}
GET /api/v1/approvals/{id}/history
```

以及 Approval Agent 后续需要：

```text
get_approval_status
get_approval_detail
list_my_recent_approvals
list_pending_approvals
```

P1-009 只先实现 REST / Service / Repository 的确定性读取能力。

Agent Tool wrappers 留到 Phase 2 / Phase 3。

---

# 4. No Migration

P1-009 预期：

```text
NO DATABASE SCHEMA CHANGE
NO ALEMBIC MIGRATION
```

现有字段已足够：

- ApprovalApplication applicant/status/workflow/current node/timestamps/version。
- MasterDataChange before/form/diff/reason/base version。
- ApprovalRecord append-only action history。
- WorkflowDefinition exact immutable version。

现有索引对 Demo 规模足够。

如果实现认为必须新增字段、索引或约束：

停止并提交 Architecture Deviation。

未经 Review 不得生成 migration。

---

# 5. Authentication Boundary

四个接口全部要求认证。

身份只能来自：

```text
AuthContext
```

禁止 query/path/body 指定任意 user_id 作为数据权限来源。

LLM 不参与任何数据权限判断。

---

# 6. GET /api/v1/approvals — My Initiated Approvals

定义：

```http
GET /api/v1/approvals
Authorization: Bearer <token>
```

语义固定为：

> 当前登录用户作为 applicant 发起的审批。

查询条件：

```text
approval.applicant_id == str(AuthContext.user_id)
```

这不是“所有我能看到的审批”。

reviewer 待办使用独立：

```text
GET /api/v1/approvals/pending
```

Query Params：

```text
status: optional ApprovalStatus
limit: default 20, min 1, max 100
offset: default 0, min 0
```

排序：

```text
created_at DESC
id DESC
```

status 仅允许 ApprovalStatus 枚举值。

---

# 7. List Pagination Contract

列表响应统一：

```json
{
  "items": [],
  "limit": 20,
  "offset": 0,
  "has_more": false
}
```

不返回 total。

Repository 使用：

```text
limit + 1
```

判断 has_more，避免额外 COUNT 查询。

P1-009 不实现 cursor pagination。

---

# 8. Approval List Item

定义等价 Pydantic：

```text
ApprovalListItem
```

至少返回：

```text
approval_id
business_type
status
applicant_id

entity_type
entity_key
change_reason

workflow_code
workflow_version

current_node_code
current_node_role

submitted_at
completed_at
created_at
updated_at
version
```

不返回：

- current_task_id
- workflow_instance_id
- target_operator_id
- password/token
- full workflow JSON
- internal stack

列表用于摘要，不返回完整 before/form/change_items。

---

# 9. My List Integrity

每条列表记录必须关联：

```text
MasterDataChange
```

且满足：

```text
business_type == MASTER_DATA_CHANGE
business_id == str(change.id)
```

否则：

```text
APPROVAL_DATA_INTEGRITY_ERROR
```

current_node_code 为 None 时：

```text
current_node_role = None
```

current_node_code 非 None 时：

- workflow_code/version 必须完整。
- exact bound workflow 必须存在且可解析。
- current node 必须存在于 bound workflow。
- current_node_role 从 exact bound workflow node.role 推导。

禁止 fallback active workflow。

---

# 10. GET /api/v1/approvals/pending — Pending For Me

定义：

```http
GET /api/v1/approvals/pending
Authorization: Bearer <token>
```

Route coarse gate：

```python
require_any_role(
    UserRole.APPROVER,
    UserRole.DATA_ADMIN,
)
```

明确：

```text
APPROVER → 可进入 Service
DATA_ADMIN → 可进入 Service
APPLICANT-only → 403
ADMIN-only → 403
```

ADMIN 不自动拥有 reviewer 权限。

---

# 11. Pending Eligibility

候选申请必须：

```text
status == IN_APPROVAL
```

然后使用 P1-007 已冻结的 assignment policy。

## 11.1 Role-Pool Task

创建当前 task 的 ApprovalRecord：

```text
target_operator_id == None
```

并且：

```text
current_node.role in AuthContext.roles
```

→ 当前用户待办。

## 11.2 Targeted Task

创建当前 task 的 ApprovalRecord：

```text
target_operator_id != None
```

必须同时：

```text
target_operator_id == str(AuthContext.user_id)
AND
current_node.role in AuthContext.roles
```

→ 当前用户待办。

同角色其他用户不能看到 targeted task 作为自己的 pending。

---

# 12. Pending Must Reuse Exact Workflow Semantics

pending 判断节点角色必须：

```text
approval.workflow_code
approval.workflow_version
→ get_by_code_version(...)
```

禁止：

```text
get_active(...)
```

如果 active v2 与 bound v1 的 node role / order 不同：

pending eligibility 仍按 v1。

---

# 13. Pending Current Task Integrity

IN_APPROVAL candidate 必须满足：

```text
workflow binding complete
workflow_instance_id non-null
current_task_id non-null
current_node_code non-null
exact workflow readable
current node valid
current task creation history exists
history.approval_id == approval.id
```

否则：

```text
APPROVAL_DATA_INTEGRITY_ERROR
```

不允许把 corruption 当作“不是我的待办”静默跳过。

原因：

静默跳过会隐藏真实待办并掩盖数据损坏。

---

# 14. Pending Pagination / Query Tradeoff

当前 workflow node role 保存在 immutable Workflow Definition JSON 中，而没有冗余写入 ApprovalApplication。

P1-009 不为此新增 schema 字段。

因此允许实现：

```text
Repository:
load deterministic IN_APPROVAL candidates + task creation records

Service:
exact workflow parse
→ role / targeted eligibility filter
→ stable sort
→ offset/limit slice
```

pending 排序固定：

```text
submitted_at ASC
created_at ASC
id ASC
```

即优先展示等待时间更长的任务。

这是一期 Demo 的明确取舍。

不得：

- 用 active workflow 做 SQL filter。
- 为了分页方便而先 SQL limit 再做权限过滤，导致页面漏掉本应属于当前用户的待办。

大规模 pending query 的 denormalized role / assignment index 优化不在 P1-009。

---

# 15. GET /api/v1/approvals/{approval_id} — Detail

定义：

```http
GET /api/v1/approvals/{approval_id}
Authorization: Bearer <token>
```

Route 使用：

```text
get_current_auth
```

而不是 ADMIN bypass 或单一角色门禁。

是否可见由 Service data permission 判断。

---

# 16. Detail Data Permission

允许查看 detail 的用户：

## 16.1 Applicant Owner

```text
actor_id == approval.applicant_id
```

→ 允许。

## 16.2 Current Eligible Reviewer

当：

```text
status == IN_APPROVAL
```

并满足 P1-007 当前任务 assignment policy：

- role-pool：node role 匹配。
- targeted：actor id + node role 同时匹配。

→ 允许。

## 16.3 Current Submit-Cycle Reviewer Participant

为了让已经处理过本轮审批节点的 reviewer 在流程继续或完成后仍可查看：

- 用户当前仍需具有 APPROVER 或 DATA_ADMIN 之一。
- 查询该 approval 最近一条 SUBMIT record 作为当前 submit cycle 起点。
- 用户必须在该 submit 之后存在 APPROVE 或 REJECT record，且 record.operator_id == actor_id。

→ 允许。

这只用于：

```text
status == IN_APPROVAL
or
status == COMPLETED
```

DRAFT 状态下：

```text
只有 applicant owner 可以查看。
```

原因：

REJECT APPLICANT / WITHDRAW 后申请人可能继续编辑草稿，历史 reviewer 不应在再次提交前看到新的未提交草稿。

ADMIN 不自动拥有读取权限。

---

# 17. Current Submit Cycle Boundary

Repository 增加等价能力：

```text
find_latest_submit_record(approval_id)

has_reviewer_participated_since(
    approval_id,
    actor_id,
    latest_submit_created_at
)
```

参与动作只计算：

```text
APPROVE
REJECT
```

不因为：

```text
target_operator_id
```

曾经指向某人，就永久授予 detail/history 权限。

latest SUBMIT 使用稳定排序：

```text
created_at DESC
id DESC
```

participant records 必须位于 current submit cycle。

如果 IN_APPROVAL / COMPLETED 且业务上需要 participant 检查，但没有任何 SUBMIT record：

```text
APPROVAL_DATA_INTEGRITY_ERROR
```

不得 fallback 到全历史。

---

# 18. Detail Forbidden / Not Found

未知 approval：

```text
APPROVAL_NOT_FOUND
HTTP 404
```

approval 存在但 actor 无数据权限：

```text
APPROVAL_FORBIDDEN
HTTP 403
```

P1-009 不改变为“无权限统一伪装 404”。

---

# 19. Approval Detail Response

定义等价：

```text
ApprovalDetailResponse
```

至少包含：

```text
approval_id
business_type
business_id
applicant_id
status

workflow_code
workflow_version
workflow_instance_id

current_node_code
current_node_role

submitted_at
completed_at
created_at
updated_at
version

change
```

不返回 current_task_id。

---

# 20. Master Data Change Detail

detail.change 至少包含：

```text
change_id
entity_type
entity_key

before_data
form_data
change_items
change_reason
base_entity_version

created_at
updated_at
```

这些字段来自数据库结构化数据。

禁止根据 LLM 文本重新生成 Diff。

P1-009 不读取或修改 MasterDataEntity 当前值作为审批结果。

---

# 21. Detail Workflow Integrity

允许两类 DRAFT：

## Initial Draft

```text
workflow_code = None
workflow_version = None
current_node_code = None
```

合法。

## Returned / Withdrawn Draft

```text
workflow binding retained
current_node_code = None
```

合法。

当 current_node_code 非空：

- workflow binding 必须完整。
- exact bound workflow 必须读取成功。
- current node 必须存在。
- role 从 exact node 推导。

当 workflow binding 只缺一半：

```text
APPROVAL_DATA_INTEGRITY_ERROR
```

禁止 fallback active workflow。

---

# 22. GET /api/v1/approvals/{approval_id}/history — History

定义：

```http
GET /api/v1/approvals/{approval_id}/history
Authorization: Bearer <token>
```

history 与 detail 使用完全相同的 data permission policy。

禁止 Router 自己复制一份权限规则。

Service 应复用同一：

```text
authorize_approval_read(...)
```

或等价 helper。

---

# 23. History Response

定义等价：

```text
ApprovalHistoryResponse
ApprovalHistoryItem
```

HistoryItem 至少返回：

```text
record_id
operator_id
action
comment

from_status
to_status

from_node_code
from_node_name
target_node_code
target_node_name

created_at
```

明确不返回：

- from_task_id
- target_task_id
- target_operator_id
- full workflow JSON
- internal assignment data

排序：

```text
created_at ASC
id ASC
```

历史为 append-only read view，不做分页。

一期单个 approval 的历史动作数量有明确上限预期，P1-009 不增加 history cursor。

---

# 24. History Integrity

ApprovalRecord 只作为 append-only fact 读取。

History 查询不修改任何 record。

如果 approval 本身 business linkage 损坏：

```text
APPROVAL_DATA_INTEGRITY_ERROR
```

如果 history 为空：

- Initial DRAFT：允许返回空数组。
- 已经 submitted / IN_APPROVAL / COMPLETED 的申请缺少历史：DATA_INTEGRITY。

对于 Returned / Withdrawn DRAFT：

应至少已有动作历史。

---

# 25. Read Service Structure

允许继续扩展：

```text
ApprovalService
```

新增等价方法：

```python
list_my_approvals(...)
list_pending_approvals(...)
get_detail(...)
get_history(...)
```

允许新增局部 read helper，例如：

```text
_validate_business_link
_resolve_bound_current_node
_resolve_current_assignment
_authorize_approval_read
_to_list_item
```

但禁止大规模重写 P1-006～P1-008 write methods。

如果代码明显需要分离 read service，先在 Pre-Implementation Plan 说明；默认仍使用 ApprovalService，避免为 P1-009 做无必要目录重构。

---

# 26. Repository Structure

ApprovalRepository 可新增只读查询：

```text
list_by_applicant(...)
list_in_approval_candidates(...)
get_application_for_read(...)
list_records(...)
find_latest_submit_record(...)
has_reviewer_participated_since(...)
```

Repository：

- SQL / ORM only。
- no AuthContext。
- no role policy。
- no workflow business logic。
- no FastAPI。
- no commit / rollback。

建议 read queries 使用 selectinload MasterDataChange，避免 async lazy loading。

---

# 27. Route Ordering

FastAPI 中：

```text
/pending
```

必须在：

```text
/{approval_id}
```

GET 动态路由之前声明。

否则：

```text
GET /api/v1/approvals/pending
```

可能被动态 UUID path 捕获并产生错误行为。

必须有 API test 锁定：

```text
/pending resolves to pending endpoint
```

---

# 28. Error Mapping

继续复用：

```text
APPROVAL_NOT_FOUND → 404
APPROVAL_FORBIDDEN → 403
APPROVAL_DATA_INTEGRITY_ERROR → 500
APPROVAL_INVALID_REQUEST → 422
```

本 TASK 不新增新的业务错误码，除非 Pre-Implementation Plan 发现必要且经 Review 批准。

FastAPI query/path structural validation 沿用现有行为。

所有 AppError 继续使用：

```text
ErrorEnvelope + request_id
```

401 继续：

```text
WWW-Authenticate: Bearer
```

---

# 29. Required Tests — Authentication / Routes

1. all four read APIs require authentication。
2. 401 includes WWW-Authenticate: Bearer。
3. 401/ErrorEnvelope includes request_id。
4. /approvals/pending resolves to static pending route。
5. APPLICANT-only pending → AUTH_PERMISSION_DENIED 403。
6. ADMIN-only pending → AUTH_PERMISSION_DENIED 403。
7. APPROVER pending route accepted。
8. DATA_ADMIN pending route accepted。
9. detail/history route only requires authentication at Route boundary。
10. Service still enforces data permission。

---

# 30. Required Tests — My List

11. only applicant-owned approvals returned。
12. another applicant's approval excluded。
13. optional status filter works。
14. invalid status query keeps FastAPI validation behavior。
15. default limit=20 / offset=0。
16. max limit 100。
17. invalid limit / offset rejected。
18. order created_at desc + id stable。
19. has_more false when no extra row。
20. has_more true using limit+1。
21. list item includes entity type/key/reason。
22. list does not expose task id / target operator。
23. initial DRAFT can list with current_node_role None。
24. returned/withdrawn DRAFT can list with retained workflow binding and role None。
25. IN_APPROVAL role derives from exact bound workflow。
26. active workflow change does not alter list current_node_role。
27. broken business linkage → DATA_INTEGRITY。
28. invalid current node / bound definition → DATA_INTEGRITY。

---

# 31. Required Tests — Pending

29. only IN_APPROVAL candidates considered。
30. role-pool APPROVER task appears to APPROVER。
31. role-pool APPROVER task excluded from DATA_ADMIN。
32. role-pool DATA_ADMIN task appears to DATA_ADMIN。
33. targeted task appears only to matching target actor with required role。
34. same-role different actor targeted task excluded。
35. matching actor without node role excluded。
36. target_operator does not bypass route role gate。
37. exact bound v1 role/order used when active v2 differs。
38. missing workflow binding → DATA_INTEGRITY。
39. missing workflow instance → DATA_INTEGRITY。
40. missing current task → DATA_INTEGRITY。
41. missing current node → DATA_INTEGRITY。
42. unknown current node → DATA_INTEGRITY。
43. missing task creation history → DATA_INTEGRITY。
44. task creation history belonging to another approval → DATA_INTEGRITY。
45. pending order is submitted_at asc / created_at asc / id asc。
46. permissions are filtered before pagination。
47. has_more computed after eligible filtering。
48. no target_operator identity exposed。

---

# 32. Required Tests — Detail Permission

49. applicant can view own initial DRAFT。
50. applicant can view own IN_APPROVAL。
51. applicant can view own COMPLETED。
52. other applicant cannot view。
53. current role-pool reviewer can view IN_APPROVAL。
54. current targeted reviewer can view IN_APPROVAL。
55. same-role non-target reviewer cannot view targeted detail。
56. wrong-role target cannot view。
57. current-cycle previous reviewer can view while later node pending。
58. current-cycle reviewer can view after COMPLETED。
59. previous-cycle reviewer cannot regain access after reject-to-applicant → edit → resubmit unless participant/current eligible in new cycle。
60. historical reviewer cannot view DRAFT after reject applicant。
61. historical reviewer cannot view DRAFT after withdraw。
62. ADMIN-only nonparticipant cannot view。
63. unknown approval → NOT_FOUND。
64. no data permission → FORBIDDEN。

---

# 33. Required Tests — Detail Content / Integrity

65. detail includes structured MasterDataChange snapshots/diff/reason。
66. detail never mutates MasterData。
67. detail current_node_role from exact workflow。
68. detail does not return current_task_id。
69. detail does not return target_operator_id。
70. initial DRAFT with no binding is valid。
71. retained-binding DRAFT with no current node is valid。
72. partial workflow binding → DATA_INTEGRITY。
73. missing exact bound workflow → DATA_INTEGRITY。
74. corrupt exact bound workflow → DATA_INTEGRITY。
75. unknown current node → DATA_INTEGRITY。
76. business type mismatch → DATA_INTEGRITY。
77. missing MasterDataChange → DATA_INTEGRITY。
78. business_id mismatch → DATA_INTEGRITY。
79. no active fallback。

---

# 34. Required Tests — History

80. history uses same authorization helper as detail。
81. applicant can view own history。
82. current eligible reviewer can view history。
83. current-cycle prior reviewer can view history。
84. unauthorized actor → FORBIDDEN。
85. initial DRAFT can return empty history。
86. submitted IN_APPROVAL with empty history → DATA_INTEGRITY。
87. COMPLETED with missing submit/action history → DATA_INTEGRITY。
88. returned/withdrawn DRAFT history remains readable by applicant。
89. history ordered created_at asc + id asc。
90. CREATE/SAVE/SUBMIT/APPROVE/REJECT/WITHDRAW records serialize stable enum values。
91. comment preserved。
92. from/to statuses preserved。
93. node snapshots preserved。
94. history does not expose task ids。
95. history does not expose target_operator_id。
96. history read is append-only / no UPDATE。

---

# 35. Required Tests — Regression

97. P1-001 tests green。
98. P1-002 tests green。
99. P1-003 tests green。
100. P1-004 tests green。
101. P1-005 tests green。
102. P1-006 tests green。
103. P1-007 tests green。
104. P1-008 tests green。
105. Ruff green。
106. format green。
107. Alembic no drift。
108. migration downgrade/re-upgrade green。
109. demo users seed green。
110. demo workflows seed green。
111. demo master data seed green。
112. frontend lint/typecheck/test/build green。

---

# 36. Read-only Transaction / Concurrency Boundary

P1-009 只读接口：

- 不新增写事务。
- 不修改 ApprovalApplication.version。
- 不新增锁。
- 不实现 snapshot isolation contract。
- 不实现缓存。

单次请求可能在并发写操作前后观察到不同已提交状态，这是普通 read committed 行为。

P1-010 再处理写操作 optimistic lock / idempotency / audit。

---

# 37. Security / Privacy Boundary

返回结构必须最小化。

禁止列表 / detail / history 暴露：

- auth token / session id
- password hash
- current task internal ID
- target operator internal assignment
- workflow full JSON
- stack trace
- internal SQL

operator_id / applicant_id 作为 Demo 业务身份可以在 detail/history 中返回。

P1-009 不实现姓名目录解析。

---

# 38. Suggested Files

预计修改：

```text
backend/app/approval/repository.py
backend/app/approval/schemas.py
backend/app/approval/service.py
backend/app/api/v1/approvals.py
backend/tests/test_approval_reads.py
```

预计不需要修改：

```text
backend/app/models/**
backend/alembic/**
backend/app/domain/approval/state_machine.py
backend/app/domain/approval/context.py
backend/app/domain/workflow/**
backend/app/api/errors.py
frontend/**
.github/workflows/ci.yml
```

如果需要新错误码或 schema/migration：

Pre-Implementation Plan 必须解释原因。

---

# 39. Frozen Decisions

1. P1-009 是 read-only TASK。
2. 四个 API：my list / pending / detail / history。
3. GET /approvals 固定为 applicant 自己发起。
4. pending route 只允许 APPROVER / DATA_ADMIN coarse gate。
5. ADMIN 无隐式业务读取权。
6. list limit/offset，limit default 20 max 100。
7. list 不返回 total。
8. my list created_at desc。
9. pending oldest-first。
10. pending 必须先做 eligibility filter 再分页。
11. pending role-pool / targeted 复用 P1-007 语义。
12. current node role 来自 exact bound workflow。
13. never fallback active workflow。
14. corrupt pending candidate 不静默跳过。
15. detail/history route 仅做 authentication，Service 做 data permission。
16. applicant owner 永远可读自己的 approval。
17. current eligible reviewer 可读 IN_APPROVAL。
18. current submit-cycle reviewer participant 可读 IN_APPROVAL / COMPLETED。
19. DRAFT 只允许 applicant owner。
20. previous-cycle reviewer 不因旧历史永久获得权限。
21. participant only counts APPROVE / REJECT after latest SUBMIT。
22. detail 返回 structured MasterDataChange。
23. detail 不返回 history；history 独立 endpoint。
24. history chronological。
25. history 不分页。
26. 不返回 task IDs / target_operator_id。
27. initial DRAFT without workflow binding 合法。
28. retained-binding DRAFT without current node 合法。
29. no schema change。
30. no migration。
31. no cache / lock / idempotency。
32. no frontend。
33. no P1-010。

任何冻结项需要改变：

停止并提交 Architecture Deviation。

---

# 40. Definition of Done

- [ ] my approvals list
- [ ] status filter
- [ ] list pagination
- [ ] pending list
- [ ] exact workflow pending eligibility
- [ ] role-pool task
- [ ] targeted task
- [ ] permission filter before pending pagination
- [ ] detail API
- [ ] structured change detail
- [ ] read data permission
- [ ] current submit-cycle participant permission
- [ ] previous-cycle permission isolation
- [ ] history API
- [ ] history safe response
- [ ] history chronological
- [ ] route ordering
- [ ] no task/target assignment leakage
- [ ] PostgreSQL integration tests
- [ ] P1-001～P1-008 regression
- [ ] no migration
- [ ] backend CI
- [ ] frontend CI
- [ ] no P1-010

---

# 41. Completion Report

Codex 最终必须输出：

```text
TASK Completion Report

TASK ID:
TASK-P1-009

Status:
READY_FOR_REVIEW

Changed files:

My approvals API:

Pending API:

Detail API:

History API:

Authentication:

My-list ownership:

Pending role-pool:

Pending targeted assignment:

Pending pagination:

Exact workflow usage:

Detail/history data permission:

Current-cycle participant:

Previous-cycle isolation:

Detail content:

History content:

Safe response boundary:

Repository reads:

Read-only behavior:

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
当前进入 TASK-P1-009 — Approval List / Pending / Detail / History APIs。

本轮不要编码。

Source of Truth：

- CODEX.md
- docs/V2.1-technical-design.md
- docs/V3.0-codex-execution-manual.md
- docs/tasks/TASK-P1-009.md

P1-009 具体范围以：

docs/tasks/TASK-P1-009.md

为最高优先级。

开始前执行：

git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short

预期：

branch:
task/p1-009

HEAD 必须包含本任务文档提交，并基于：

main:
35a257f522708fb6a5e7b048fc257912cf2ddf8e

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
- docs/tasks/TASK-P1-009.md
- backend/app/models/**
- backend/app/domain/approval/**
- backend/app/domain/workflow/**
- backend/app/approval/**
- backend/app/api/**
- backend/app/auth/**
- backend/app/master_data/**
- backend/app/workflow/**
- backend/tests/**
- .github/workflows/ci.yml

重点阅读：

- P1-007 role-pool / targeted task assignment
- ApprovalRepository.get_task_creation_record
- ApprovalService._load_action_context
- P1-008 withdraw → DRAFT → resubmit semantics
- exact bound workflow behavior

本轮只输出：

# Pre-Implementation Plan — TASK-P1-009

至少覆盖：

1. TASK goal / boundary
2. files to add / modify
3. why no migration
4. GET /approvals semantics
5. status / limit / offset
6. list ordering / has_more
7. ApprovalListItem safe fields
8. my-list ownership
9. my-list integrity
10. GET /pending route gate
11. pending candidate query
12. role-pool eligibility
13. targeted eligibility
14. exact bound workflow
15. no active fallback
16. current task history integrity
17. permission filter before pagination
18. pending ordering
19. pending scaling tradeoff
20. GET /{id} detail
21. route ordering with /pending
22. applicant read permission
23. current eligible reviewer read permission
24. current submit-cycle participant permission
25. latest SUBMIT cycle boundary
26. previous-cycle reviewer isolation
27. DRAFT read restriction
28. ADMIN behavior
29. detail response fields
30. structured MasterDataChange output
31. current node role resolution
32. initial DRAFT semantics
33. retained-binding DRAFT semantics
34. GET /{id}/history
35. shared authorization helper
36. history safe fields
37. history ordering
38. history integrity rules
39. repository queries
40. read-only transaction behavior
41. PostgreSQL integration tests
42. API tests
43. regression / CI
44. concurrency/read consistency limitation
45. security / privacy
46. architecture risks / reviewer questions
47. Out of Scope
48. how scope avoids P1-010

不要编码。
不要生成 migration。
不要修改文件。
不要安装依赖。

等待 Architecture Review APPROVE。
```
