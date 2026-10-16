# TASK-P1-010 — Optimistic Lock + Idempotency + Audit

## 0. Status

```text
TASK ID: P1-010
Name: Optimistic Lock + Idempotency + Audit
Phase: Phase 1 — Approval Backend
Status: DESIGN FROZEN / WAITING FOR PRE-IMPLEMENTATION PLAN
```

本文件是 TASK-P1-010 的具体执行 Source of Truth。

如本文件与 V2.1 / V3.0 中历史示例存在差异，以本文件和已经合并的 P1-001～P1-009 实现为准。

开始基线：

```text
main:
759c926d7f4640f790828ac279cc1d318c2e5d0e

P1-009:
Approval List / Pending / Detail / History APIs
```

P1-010 是 Phase 1 最后一个 TASK。

完成并合并后才允许进入 Phase 2 Tool Infrastructure。

---

# 1. Goal

为 Phase 1 已有写链路补齐企业级写操作保护：

```text
Optimistic Lock
+
Idempotency
+
Durable Audit
```

覆盖：

```text
Master Data Draft CREATE
Master Data Draft SAVE

Approval SUBMIT
Approval APPROVE
Approval REJECT
Approval WITHDRAW
```

核心目标：

- 所有对现有 ApprovalApplication 的修改使用 version CAS。
- 防止两个客户端基于同一旧 version 同时成功写入。
- 重复 Idempotency-Key + 同一语义请求返回第一次成功结果。
- 相同 key 被复用于不同 action/resource/payload 时稳定拒绝。
- 首次成功写操作产生一次 durable audit。
- replay 不重复 ApprovalRecord、不重复 audit、不重复 version increment。
- business write + ApprovalRecord + Audit + Idempotency result 保持同一事务原子性。
- 主数据草稿在 SAVE / SUBMIT 时检测 base entity version 是否已过期。

---

# 2. Explicit Out of Scope

P1-010 禁止实现：

- final approval 后写回 MasterDataEntity
- 新审批状态
- 修改 P1-002 State Machine
- confirmation token
- HITL
- Tool / ToolExecutor
- Agent / LLM / MCP
- Redis distributed lock
- message queue
- audit query UI / audit REST API
- frontend changes
- background cleanup scheduler
- generalized workflow engine
- P2 Tool Infrastructure

不得为了实现并发控制而引入悲观锁作为主方案。

---

# 3. Existing Version Semantics

当前已有：

```text
ApprovalApplication.version
MasterDataEntity.version
MasterDataChange.base_entity_version
```

P1-010 冻结语义：

## ApprovalApplication.version

```text
CREATE draft:
version = 1

SAVE:
version + 1

SUBMIT:
version + 1

APPROVE:
version + 1

REJECT:
version + 1

WITHDRAW:
version + 1
```

只有首次真实成功执行的写动作才递增。

idempotent replay：

```text
不递增
```

失败 / rollback：

```text
不递增
```

---

# 4. Optimistic Lock Must Be Real CAS

禁止只做：

```python
if approval.version != expected_version:
    raise ...
approval.status = ...
flush()
```

因为：

```text
两个事务都可能先读到相同 version
→ 都通过 Python 判断
→ lost update
```

必须使用数据库 compare-and-swap：

```sql
UPDATE approval_application
SET
    ...,
    version = version + 1
WHERE id = :approval_id
  AND version = :expected_version
```

以 rowcount / RETURNING 判断是否成功。

若 0 rows：

```text
VERSION_CONFLICT
HTTP 409
```

P1-010 不使用 `SELECT FOR UPDATE` 代替 optimistic lock。

---

# 5. Expected Version — Approval APIs

Approval 写 API 都必须携带：

```text
expected_version
```

新增等价请求：

```text
SubmitApprovalRequest
```

修改：

```text
ApproveRequest
RejectRequest
WithdrawRequest
```

语义：

```text
expected_version = 客户端最后读取到的 ApprovalApplication.version
```

要求：

- positive integer。
- bool 不视为合法 version。
- 缺失 / 非法 → APPROVAL_INVALID_REQUEST 422。
- stale → APPROVAL_VERSION_CONFLICT 409。

---

# 6. Approval Responses Must Return Version

修改：

```text
SubmitApprovalResponse
ApprovalActionResponse
```

新增：

```text
version
```

返回的是：

```text
成功 CAS 后的新 version
```

例如：

```text
before version = 3
SUBMIT success
response.version = 4
```

P1-009 list/detail 已有 version，无需改语义。

---

# 7. Expected Version — Draft UPDATE

`ChangeDraftRequest` 增加：

```text
expected_version: optional
```

冻结规则：

## Create draft

```text
draft_approval_id == None
expected_version must be None
```

创建成功：

```text
approval.version = 1
```

## Update existing draft

```text
draft_approval_id != None
expected_version is required
```

成功 SAVE：

```text
ApprovalApplication.version + 1
```

stale：

```text
MASTER_DATA_VERSION_CONFLICT
HTTP 409
```

`ChangeDraftResponse` 增加：

```text
approval_version
```

---

# 8. MasterDataEntity Baseline Protection

当前 Phase 1 不存在合法的：

```text
final approve → UPDATE MasterDataEntity
```

路径。

因此 P1-010 不凭空实现 final writeback。

但是 P1-005 已保存：

```text
MasterDataChange.base_entity_version
```

P1-010 必须在：

```text
SAVE existing draft
SUBMIT approval
```

校验：

```text
current MasterDataEntity.version
==
MasterDataChange.base_entity_version
```

若不相等：

```text
stale draft
→ VERSION CONFLICT 409
```

映射：

SAVE：

```text
MASTER_DATA_VERSION_CONFLICT
```

SUBMIT：

```text
APPROVAL_VERSION_CONFLICT
```

不得自动 rebase before/form/diff。

真正对 MasterDataEntity 的 CAS UPDATE 要等未来明确引入 writeback 功能后实现。

---

# 9. Idempotency Scope

以下全部写 API 要求：

```http
Idempotency-Key: <key>
```

覆盖：

```text
POST /api/v1/master-data/{type}/{key}/changes

POST /api/v1/approvals/{id}/submit
POST /api/v1/approvals/{id}/approve
POST /api/v1/approvals/{id}/reject
POST /api/v1/approvals/{id}/withdraw
```

缺失 / 非法 key：

```text
stable Idempotency AppError
HTTP 422
```

建议：

```text
1..200 chars
strip 后不能为空
```

key 大小写敏感。

---

# 10. Idempotency Binding

Idempotency record 必须绑定：

```text
actor_id
key
action
resource_type
resource_id
request_hash
```

唯一性：

```text
(actor_id, key)
```

意味着：

- 同一个用户重复同 key → 检查绑定。
- 不同用户可以使用相同 key。

同 actor + key：

## Same semantic request

```text
same action
same resource
same canonical request hash
```

→ replay first successful response。

## Different request

任一变化：

```text
action
resource
canonical payload
expected_version
```

→ IDEMPOTENCY_CONFLICT 409。

---

# 11. Resource Binding

Approval actions：

```text
resource_type = approval
resource_id = approval_id
```

Draft UPDATE：

```text
resource_type = approval
resource_id = draft_approval_id
```

Draft CREATE：

审批 ID 尚未生成，所以 idempotency scope 使用：

```text
resource_type = master_data_entity
resource_id = canonical(entity_type, entity_key)
```

成功后 response 中保存真实 approval_id。

同 create key replay：

```text
必须返回相同 approval_id
不得创建第二个 draft
```

---

# 12. Canonical Request Hash

使用稳定 JSON canonicalization + SHA-256 或等价稳定算法。

hash 输入只包括业务语义字段。

必须在业务 normalization 后计算。

例如：

Approve：

```text
expected_version
normalized comment
```

Reject：

```text
expected_version
normalized target
normalized comment
```

Withdraw：

```text
expected_version
normalized comment
```

Submit：

```text
expected_version
```

Draft：

```text
draft_approval_id
expected_version
validated changes
normalized reason
entity_type
entity_key
```

相同语义：

```text
"  同意  "
"同意"
```

在 normalization 后应得到同一 request hash。

不得 hash auth token / password / raw secret。

---

# 13. Idempotency Model

新增持久化表，等价：

```text
idempotency_records
```

至少包含：

```text
id
actor_id
key
action
resource_type
resource_id
request_hash
response_json nullable
expires_at
created_at
updated_at
```

`response_json`：

- claim 时可为 NULL。
- business success 后在同一 transaction 写入安全响应。
- replay 时通过对应 Pydantic response model 反序列化。

禁止持久化 auth token / password。

---

# 14. Concurrency-Safe Idempotency Claim

不得实现成：

```text
SELECT key
if none:
    do business
    INSERT key
```

因为两个并发请求都可能先 SELECT none。

必须使用数据库唯一约束 + 原子 claim。

推荐 PostgreSQL：

```text
INSERT ... ON CONFLICT ...
RETURNING
```

或等价安全方案。

并发 identical request：

```text
request A → claim → execute → commit result
request B → waits/conflicts → replay A result
```

最终必须：

```text
one business transition
one ApprovalRecord
one AuditLog
one version increment
same response
```

---

# 15. Idempotency Expiration

记录必须有：

```text
expires_at
```

P1-010 默认 TTL：

```text
24 hours
```

过期 key 可以重新 claim。

不实现 background cleanup scheduler。

允许在 claim 时原子 reclaim expired row。

必须避免：

```text
delete expired
→ race
→ duplicate execution
```

如果实现无法证明 expiration reclaim 并发安全：

停止并提交 Architecture Deviation。

---

# 16. Replay Ordering

Idempotency binding/replay 必须发生在状态机、version CAS 之前。

原因：

第一次：

```text
version 2
→ approve
→ version 3
```

客户端因网络超时以：

```text
same key
same expected_version=2
```

重放。

正确：

```text
replay first response version=3
```

不能先做：

```text
expected_version 2 vs current 3
→ VERSION_CONFLICT
```

同 key + same request 的 replay 必须优先返回第一次结果。

---

# 17. Failed Requests Do Not Consume Key

如果业务执行失败：

```text
FORBIDDEN
INVALID_TRANSITION
VERSION_CONFLICT
DATA_INTEGRITY
validation error
unexpected transaction failure
```

idempotency claim 必须随 transaction rollback。

结果：

```text
key 不被永久消费
```

用户修正状态或数据后仍可重新执行。

只有成功 transaction 才产生可 replay record。

---

# 18. Audit Model

新增持久化表，等价：

```text
audit_logs
```

至少包含：

```text
id
actor_id
action
resource_type
resource_id
metadata_json
created_at
```

audit append-only。

生产代码：

```text
no UPDATE audit
no DELETE audit
```

测试 cleanup 可以直接 SQL 删除测试数据。

---

# 19. Audit Scope

首次成功写操作必须生成 1 条 durable audit：

```text
MASTER_DATA_DRAFT_CREATE
MASTER_DATA_DRAFT_SAVE
APPROVAL_SUBMIT
APPROVAL_APPROVE
APPROVAL_REJECT
APPROVAL_WITHDRAW
```

replay：

```text
不新增 audit
```

失败：

```text
不新增 committed audit
```

P1-010 audit 只覆盖成功业务写。

拒绝/失败尝试的安全事件审计留后续 observability/security hardening。

---

# 20. Audit Metadata Safety

允许 metadata：

```text
approval_id
change_id
entity_type
entity_key
from_status
to_status
from_version
to_version
workflow_code
workflow_version
from_node_code
target_node_code
```

按动作选择最小子集。

禁止 metadata 原文记录：

- auth token
- password
- session token
- full before_data
- full form_data
- raw changes
- bank account
- tax id
- comment text
- change reason text
- full request body

audit 必须是可展示的安全元数据，而不是请求 dump。

---

# 21. Transaction Atomicity

首次执行事务顺序等价：

```text
BEGIN

validate/normalize request

claim idempotency key
if replay:
    return stored response

load business state
validate permission/state/integrity
validate expected_version
validate base entity version where required

CAS UPDATE ApprovalApplication
append ApprovalRecord
update MasterDataChange if SAVE
append AuditLog

build response
persist idempotency response_json

COMMIT
```

任何一步异常：

```text
ROLLBACK everything
```

不能出现：

- application 已变但 ApprovalRecord 没写。
- history 写了但 audit 没写。
- business 写成功但 idempotency result 丢失。
- idempotency committed 但 business rolled back。

---

# 22. Approval CAS Repository Boundary

ApprovalRepository 增加等价 CAS 能力：

```text
update_application_if_version(...)
```

要求：

- SQL `WHERE id AND version`。
- CAS 内完成 `version = version + 1`。
- 返回更新后 version / row。
- 0 row → Service 映射 VERSION_CONFLICT。
- Repository 不判断业务状态。
- Repository 不 commit / rollback。

现有 write Service 不得只靠 ORM dirty flush 更新 ApprovalApplication。

---

# 23. Draft SAVE CAS Boundary

MasterDataRepository 增加等价：

```text
bump_approval_version_if_match(...)
```

或复用批准的共享 CAS repository 方案。

要求：

```text
WHERE approval_id
AND version = expected_version
SET version = version + 1
```

SAVE 修改：

```text
MasterDataChange
+
ApprovalApplication.version
+
ApprovalRecord SAVE
+
Audit
+
Idempotency result
```

必须同 transaction。

---

# 24. Write Action Version Behavior

## Draft CREATE

```text
version = 1
```

## Draft SAVE

```text
expected V
CAS
→ V+1
```

## SUBMIT

```text
expected V
CAS state/runtime + version
→ V+1
```

## APPROVE / REJECT / WITHDRAW

同样：

```text
expected V
CAS transition + version
→ V+1
```

ApprovalRecord 本 TASK 不新增 version 字段。

Audit metadata 记录：

```text
from_version
to_version
```

---

# 25. Concurrent Write Rules

必须有 PostgreSQL 并发测试。

场景：

```text
same approval version = 5

request A expected=5
request B expected=5
different idempotency keys
```

期望：

```text
exactly one succeeds
other → VERSION_CONFLICT

final version = 6

exactly one business ApprovalRecord
exactly one AuditLog
```

不能：

```text
both succeed
version = 7
```

---

# 26. Concurrent Same-Key Rules

场景：

```text
same actor
same Idempotency-Key
same request
two concurrent sessions
```

期望：

```text
both caller results semantically identical

one actual mutation
one version increment
one ApprovalRecord
one AuditLog
one durable idempotency result
```

Draft CREATE 也必须覆盖。

---

# 27. Different Key + Same Request

```text
key A + expected version V
→ success to V+1

key B + same expected version V
→ VERSION_CONFLICT
```

Idempotency 不能把不同 key 的请求自动视为 replay。

---

# 28. Same Key + Different Request

同 actor/key，但改变：

- expected_version
- action
- resource
- reject target
- comment
- draft patch / reason

任一语义：

```text
IDEMPOTENCY_CONFLICT
HTTP 409
```

不得执行 business mutation。

---

# 29. Error Model

新增稳定错误：

Approval：

```text
APPROVAL_VERSION_CONFLICT
→ 409
```

Master Data：

```text
MASTER_DATA_VERSION_CONFLICT
→ 409
```

Idempotency：

```text
IDEMPOTENCY_KEY_REQUIRED
→ 422

IDEMPOTENCY_KEY_INVALID
→ 422

IDEMPOTENCY_CONFLICT
→ 409

IDEMPOTENCY_DATA_INTEGRITY_ERROR
→ 500
```

全部使用：

```text
ErrorEnvelope + request_id
```

Pydantic structural validation 仍可保留 FastAPI 默认 422。

---

# 30. API Contract

Approval routes：

```http
Idempotency-Key: required
```

Submit body：

```json
{
  "expected_version": 2
}
```

Approve：

```json
{
  "expected_version": 3,
  "comment": "同意"
}
```

Reject：

```json
{
  "expected_version": 3,
  "target": "APPLICANT",
  "comment": "请修改"
}
```

Withdraw：

```json
{
  "expected_version": 3,
  "comment": "需要修改"
}
```

Draft CREATE：

```json
{
  "draft_approval_id": null,
  "expected_version": null,
  "changes": {...},
  "reason": "..."
}
```

Draft UPDATE：

```json
{
  "draft_approval_id": "...",
  "expected_version": 2,
  "changes": {...},
  "reason": "..."
}
```

---

# 31. Service Signatures

允许调整等价：

```python
submit(
    approval_id,
    auth_context,
    expected_version,
    idempotency_key,
)

approve(
    approval_id,
    auth_context,
    expected_version,
    comment,
    idempotency_key,
)

reject(
    approval_id,
    auth_context,
    expected_version,
    target,
    comment,
    idempotency_key,
)

withdraw(
    approval_id,
    auth_context,
    expected_version,
    comment,
    idempotency_key,
)
```

MasterData：

```text
create_or_update_draft(...)
```

从 payload 读取 expected_version，并接收 idempotency_key。

不保留绕过 P1-010 的“legacy no-key/no-version write mode”。

现有 P1-005～P1-008 tests 必须迁移到新 contract。

---

# 32. Idempotency / Audit Module Boundary

推荐新增独立 cross-cutting 模块，例如：

```text
app/idempotency/
app/audit/
app/models/governance.py
```

或等价清晰结构。

要求：

Idempotency：

- 无 FastAPI 依赖。
- 无 Approval StateMachine 依赖。
- 无 LLM / Tool 依赖。
- 可被未来 ToolExecutor 复用。

Audit：

- 只负责 append safe audit fact。
- 无业务权限判断。
- 无 request body dump。
- 可被未来 ToolExecutor / service 复用。

如果选择其他目录结构，Pre-Implementation Plan 必须解释。

---

# 33. Migration

P1-010 必须新增 Alembic migration。

只允许新增：

```text
idempotency_records
audit_logs
```

以及它们必要的：

- unique constraint
- indexes
- foreign-key-free cross-cutting scalar fields

不得：

- 修改 Approval 状态 enum。
- 修改 Workflow schema。
- 删除现有字段。
- 为 P2 增加 tables。

Migration 必须：

```text
upgrade from current head
downgrade back to P1-009 head
re-upgrade head
alembic check no drift
```

---

# 34. Idempotency Table Constraints / Indexes

至少：

```text
UNIQUE(actor_id, key)
```

建议 index：

```text
expires_at
(resource_type, resource_id)
```

request_hash 固定 SHA-256 hex：

```text
length 64
```

response_json：

```text
JSONB nullable
```

---

# 35. Audit Table Indexes

建议：

```text
actor_id
(resource_type, resource_id)
created_at
action
```

P1-010 不新增 audit query API，但索引为后续 trace/audit read 做准备。

---

# 36. Required Tests — API Contract

1. master-data write without Idempotency-Key → 422 stable idempotency error。
2. submit without Idempotency-Key → 422。
3. approve without Idempotency-Key → 422。
4. reject without Idempotency-Key → 422。
5. withdraw without Idempotency-Key → 422。
6. blank / too-long idempotency key rejected。
7. ErrorEnvelope contains request_id。
8. submit missing expected_version → 422。
9. approve missing expected_version → 422。
10. reject missing expected_version → 422。
11. withdraw missing expected_version → 422。
12. draft UPDATE missing expected_version → MASTER_DATA invalid/version error。
13. draft CREATE with expected_version provided → rejected。
14. response includes new version / approval_version。
15. existing auth role gates remain unchanged。

---

# 37. Required Tests — Optimistic Lock

16. draft starts at approval version 1。
17. SAVE expected=1 → approval_version 2。
18. second SAVE expected=1 → MASTER_DATA_VERSION_CONFLICT。
19. SUBMIT expected current V → V+1。
20. stale SUBMIT → APPROVAL_VERSION_CONFLICT。
21. APPROVE success increments once。
22. stale APPROVE → conflict。
23. REJECT success increments once。
24. stale REJECT → conflict。
25. WITHDRAW success increments once。
26. stale WITHDRAW → conflict。
27. failed state-machine transition does not increment。
28. permission failure does not increment。
29. rollback after CAS restores old version。
30. CAS SQL prevents two concurrent stale writers both succeeding。

---

# 38. Required Tests — Master Data Baseline

31. draft CREATE stores current MasterDataEntity.version。
32. entity version unchanged → SAVE allowed。
33. simulate entity.version bump → SAVE conflict。
34. simulate entity.version bump → SUBMIT conflict。
35. conflict does not rebase before_data。
36. conflict does not mutate form_data/diff。
37. conflict does not append business record/audit。
38. current backend still never writes MasterDataEntity on final approve。

---

# 39. Required Tests — Idempotency Replay

39. same key + same draft CREATE → same approval_id。
40. one CREATE record only。
41. one audit only。
42. one idempotency result only。
43. same key + same SAVE → same response。
44. SAVE version increment once。
45. same key + same SUBMIT → same response。
46. SUBMIT record once。
47. same key + same APPROVE → same response。
48. APPROVE record once。
49. same key + same REJECT → same response。
50. REJECT record once。
51. same key + same WITHDRAW → same response。
52. WITHDRAW record once。
53. replay after resource current version changed still returns first result。
54. replay never creates new audit。
55. replay never increments version。

---

# 40. Required Tests — Idempotency Conflict

56. same actor/key different action → conflict。
57. same actor/key different resource → conflict。
58. same actor/key different expected_version → conflict。
59. same actor/key different normalized comment → conflict。
60. same actor/key different reject target → conflict。
61. same actor/key different draft changes → conflict。
62. same actor/key different draft reason → conflict。
63. different actor may reuse same key。
64. different key same stale request → version conflict, not replay。

---

# 41. Required Tests — Idempotency Concurrency / Expiry

65. concurrent same-key approval write → one mutation, two equivalent results。
66. concurrent same-key draft CREATE → one draft。
67. concurrent different-key same-version approval write → one success / one version conflict。
68. unexpired same key replays。
69. expired key can be atomically reclaimed。
70. expired reclaim does not produce duplicate committed mutation under concurrency。
71. transaction failure does not leave committed claim。
72. committed idempotency row never has NULL response_json。
73. request hash deterministic for reordered JSON object keys。
74. normalized whitespace comments hash identically。

---

# 42. Required Tests — Audit

75. CREATE success → exactly one audit。
76. SAVE success → exactly one audit。
77. SUBMIT success → exactly one audit。
78. APPROVE success → exactly one audit。
79. REJECT success → exactly one audit。
80. WITHDRAW success → exactly one audit。
81. action names stable。
82. actor_id trusted from AuthContext。
83. resource id correct。
84. metadata includes from/to version where applicable。
85. metadata includes safe status/node information where applicable。
86. metadata does not contain comment。
87. metadata does not contain reason。
88. metadata does not contain before/form/raw changes。
89. replay produces no second audit。
90. failed write produces no committed audit。
91. injected audit failure rolls back business mutation。
92. audit production repository exposes no update/delete mutation API。

---

# 43. Required Tests — Atomicity

93. failure after CAS but before ApprovalRecord → all rollback。
94. failure after ApprovalRecord flush → all rollback。
95. failure after Audit flush → all rollback。
96. failure before idempotency completion → all rollback。
97. no committed idempotency claim on rollback。
98. no partial MasterDataChange mutation。
99. no partial runtime mutation。
100. no version leak on rollback。

---

# 44. Required Tests — Migration

101. migration creates idempotency_records。
102. migration creates audit_logs。
103. unique(actor_id,key) enforced。
104. downgrade removes only P1-010 tables。
105. P1-009 schema preserved after downgrade。
106. re-upgrade succeeds。
107. alembic check reports no drift。
108. empty DB upgrade head succeeds。

---

# 45. Required Tests — Regression

109. P1-001 tests green。
110. P1-002 tests green。
111. P1-003 tests green。
112. P1-004 tests green。
113. P1-005 tests updated and green。
114. P1-006 tests updated and green。
115. P1-007 tests updated and green。
116. P1-008 tests updated and green。
117. P1-009 tests green。
118. Ruff green。
119. format green。
120. demo users seed green。
121. demo workflows seed green。
122. demo master data seed green。
123. frontend lint/typecheck/test/build green。

---

# 46. Security Boundary

Idempotency key：

- 不作为身份。
- 不授予权限。
- 不替代 expected_version。
- 不允许跨 actor replay。

Audit：

- actor 来自 AuthContext。
- metadata 白名单构造。
- 不 dump request。
- 不记录 token。
- 不记录 comment/reason/raw business payload。

Optimistic lock：

- expected_version 来自客户端。
- current version 必须由数据库 CAS 判定。
- LLM 不参与。

---

# 47. Retry Semantics

P1-010 只提供“写操作可安全重放”的基础能力。

不实现自动 retry loop。

未来 ToolExecutor 可以基于：

```text
Idempotency-Key
+
retry policy
```

进行写 Tool retry。

业务错误：

```text
permission
validation
invalid transition
version conflict
```

未来也不得盲目自动 retry。

---

# 48. Read API Behavior

P1-009 read APIs：

- 不需要 Idempotency-Key。
- 不需要 expected_version。
- 不产生 audit。
- 返回最新 committed version。

本 TASK 不改变它们的数据权限。

---

# 49. Suggested Files

预计新增：

```text
backend/app/models/governance.py

backend/app/idempotency/__init__.py
backend/app/idempotency/errors.py
backend/app/idempotency/repository.py
backend/app/idempotency/service.py

backend/app/audit/__init__.py
backend/app/audit/repository.py

backend/alembic/versions/<new_revision>_p1_010_idempotency_audit.py

backend/tests/test_p1_010_governance.py
```

预计修改：

```text
backend/app/models/__init__.py or metadata import path
backend/app/api/errors.py
backend/app/api/v1/approvals.py
backend/app/api/v1/master_data.py

backend/app/approval/errors.py
backend/app/approval/repository.py
backend/app/approval/schemas.py
backend/app/approval/service.py

backend/app/master_data/errors.py
backend/app/master_data/repository.py
backend/app/master_data/schemas.py
backend/app/master_data/service.py

existing P1-005～P1-008 tests
CI migration regression if current head-specific step requires extension
```

具体文件结构允许 Codex 在 Plan 中做小范围调整，但不得改变本 TASK 的模型、事务和安全语义。

---

# 50. Frozen Decisions

1. P1-010 是 Phase 1 最后任务。
2. ApprovalApplication.version 对 SAVE/SUBMIT/APPROVE/REJECT/WITHDRAW 每次首次成功 +1。
3. CREATE version=1。
4. CAS 必须是 DB UPDATE WHERE version。
5. no pessimistic-lock substitution。
6. Approval writes require expected_version。
7. Draft UPDATE requires expected_version。
8. Draft CREATE must not send expected_version。
9. successful write responses return new version。
10. SAVE/SUBMIT validate MasterDataEntity baseline version。
11. no final MasterData writeback。
12. all write POST APIs require Idempotency-Key。
13. idempotency unique scope = actor + key。
14. key binds action/resource/request hash。
15. same request replay first result。
16. different request same key → 409。
17. replay happens before version/state validation。
18. failed request does not consume key。
19. concurrent claim must be DB-safe。
20. TTL=24h。
21. expired key atomically reusable。
22. audit only successful first executions。
23. replay does not audit again。
24. audit in same transaction as business mutation。
25. audit metadata uses explicit safe allowlist。
26. idempotency response persisted in same transaction。
27. no read audit in P1-010。
28. no Tool/Agent/HITL。
29. migration only adds idempotency/audit tables。
30. no P2 work before merge。

任何冻结项需要改变：

停止并提交 Architecture Deviation。

---

# 51. Definition of Done

- [ ] migration for idempotency + audit
- [ ] optimistic CAS ApprovalApplication
- [ ] draft SAVE CAS
- [ ] expected version API contract
- [ ] response version contract
- [ ] master data base-version guard
- [ ] idempotency required on all write APIs
- [ ] atomic idempotency claim
- [ ] request fingerprint
- [ ] replay result
- [ ] conflict detection
- [ ] 24h expiry
- [ ] concurrent same-key test
- [ ] concurrent stale-version test
- [ ] audit append
- [ ] safe audit metadata
- [ ] audit transaction rollback
- [ ] no replay duplicate history/audit
- [ ] P1-001～P1-009 regression
- [ ] Alembic downgrade/re-upgrade
- [ ] backend CI
- [ ] frontend CI
- [ ] no Phase 2 code

---

# 52. Completion Report

Codex 最终必须输出：

```text
TASK Completion Report

TASK ID:
TASK-P1-010

Status:
READY_FOR_REVIEW

Changed files:

Migration:

Optimistic-lock strategy:

CAS SQL / repository:

Approval version semantics:

Draft version semantics:

MasterData baseline guard:

Expected-version API contract:

Response version contract:

Idempotency model:

Idempotency key validation:

Canonical request hash:

Atomic claim:

Replay behavior:

Conflict behavior:

Expiry behavior:

Concurrent same-key behavior:

Concurrent stale-version behavior:

Audit model:

Audit actions:

Audit metadata safety:

Transaction atomicity:

Rollback tests:

Migration downgrade/re-upgrade:

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
Phase 2 Tool Infrastructure only after review + merge
```

不得写 DONE。

---

# 53. Codex Pre-Implementation Plan Prompt

```text
当前进入 TASK-P1-010 — Optimistic Lock + Idempotency + Audit。

本轮不要编码。

Source of Truth：

- CODEX.md
- docs/V2.1-technical-design.md
- docs/V3.0-codex-execution-manual.md
- docs/tasks/TASK-P1-010.md

P1-010 具体范围以：

docs/tasks/TASK-P1-010.md

为最高优先级。

开始前执行：

git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short

预期：

branch:
task/p1-010

HEAD 必须包含本任务文档提交，并基于：

main:
759c926d7f4640f790828ac279cc1d318c2e5d0e

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
- docs/tasks/TASK-P1-010.md
- backend/app/models/**
- backend/app/domain/approval/**
- backend/app/approval/**
- backend/app/master_data/**
- backend/app/api/**
- backend/app/core/**
- backend/app/infrastructure/**
- backend/alembic/**
- backend/tests/**
- .github/workflows/ci.yml

重点理解：

- P1-005 MasterData draft CREATE / SAVE transaction
- P1-006 SUBMIT transaction
- P1-007 APPROVE / REJECT runtime semantics
- P1-008 WITHDRAW semantics
- P1-009 version read fields
- ApprovalApplication.version
- MasterDataEntity.version
- MasterDataChange.base_entity_version

本轮只输出：

# Pre-Implementation Plan — TASK-P1-010

至少覆盖：

1. TASK goal / boundary
2. exact files add / modify
3. migration design
4. idempotency table columns / constraints / indexes
5. audit table columns / indexes
6. metadata import / Alembic discoverability
7. ApprovalApplication version semantics
8. CREATE version behavior
9. SAVE version behavior
10. SUBMIT version behavior
11. APPROVE version behavior
12. REJECT version behavior
13. WITHDRAW version behavior
14. DB CAS implementation
15. why Python version check alone is insufficient
16. repository CAS API
17. concurrent stale writer scenario
18. expected_version request changes
19. response version changes
20. draft UPDATE expected version
21. draft CREATE expected-version prohibition
22. MasterData base entity version guard
23. why no final MasterData writeback
24. Idempotency-Key validation
25. actor+key unique scope
26. action/resource binding
27. draft CREATE resource binding
28. canonical request fingerprint
29. normalized payload hashing
30. atomic claim SQL
31. concurrent identical-key behavior
32. replay before version/state validation
33. same-key conflict behavior
34. failed request key rollback
35. 24h TTL
36. concurrency-safe expired reclaim
37. response_json serialization/deserialization
38. audit actions
39. audit safe metadata allowlist
40. no sensitive request dump
41. audit same-transaction behavior
42. replay no duplicate audit/history/version
43. injected failure rollback points
44. API error codes / HTTP mapping
45. P1-005～P1-008 test migration
46. new PostgreSQL integration tests
47. true concurrency tests
48. migration downgrade / re-upgrade
49. alembic check
50. regression / CI
51. performance / indexes
52. architecture risks / reviewer questions
53. Out of Scope
54. why no Phase 2 code

不要编码。
不要生成 migration。
不要修改文件。
不要安装依赖。

等待 Architecture Review APPROVE。
```
