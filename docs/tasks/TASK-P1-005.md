# TASK-P1-005 — MasterDataEntity + Change Draft / Diff

## 0. Status

```text
TASK ID: P1-005
Name: MasterDataEntity + Change Draft / Diff
Phase: Phase 1 — Approval Backend
Status: DESIGN FROZEN / WAITING FOR PRE-IMPLEMENTATION PLAN
```

本文件是 TASK-P1-005 的具体执行 Source of Truth。

如本文件与 V2.1 / V3.0 中历史示例或 P1-001 初始占位字段存在冲突，以本文件和已经合并的 P1-001～P1-004 实现为准。

开始基线：

```text
main:
31353d749d09bd14d7d7301add441e7ef558977b

P1-001:
Approval Domain Models

P1-002:
Approval State Machine

P1-003:
Demo Users / Authentication / Roles

P1-004:
Workflow Definition
```

P1-005 不重新设计前四个 TASK。

---

# 1. Goal

建立一个可独立运行的 Demo 主数据变更草稿能力：

```text
读取当前主数据
    ↓
接收字段 patch
    ↓
生成完整 after snapshot
    ↓
生成结构化 Diff
    ↓
创建 / 更新 DRAFT 审批草稿
    ↓
等待 P1-006 submit
```

本 TASK 只负责：

- 当前主数据实体。
- 变更草稿。
- before / after snapshot。
- deterministic diff。
- DRAFT 审批容器。
- Draft create / update。
- 对应 REST API。
- Demo master data seed。

本 TASK **不提交审批**。

---

# 2. Relationship to V2.1

V2.1 明确要求：

```text
master_data_entities:
id, entity_type, entity_key, data_json, version

master_data_changes:
id, entity_id, before_json, after_json, diff_json, reason
```

并定义 Agent 路径：

```text
get_master_data
→ ...
→ create_change_draft
→ preview_diff
→ WAIT_CONFIRMATION
→ submit_for_approval
```

当前 P1-001 已经提前建立了：

```text
master_data_change
```

字段：

```text
approval_id
entity_type
entity_id
form_data
ext_data
change_items
change_reason
```

P1-005 不新建第二张重复 Change 表。

本 TASK 冻结以下语义映射：

```text
before snapshot  -> master_data_change.before_data
after snapshot   -> master_data_change.form_data
diff             -> master_data_change.change_items
reason           -> master_data_change.change_reason
entity key       -> master_data_change.entity_key
base version     -> master_data_change.base_entity_version
```

其中：

- `form_data` 继续作为完整 after snapshot。
- `change_items` 继续作为结构化 diff。
- `ext_data` 保留，P1-005 不使用，不删除。
- 原 `entity_id` 重命名为 `entity_key`，保持数据值不变。
- 新增 `before_data` 和 `base_entity_version`。

这样既保留 P1-001 已批准的模型，又满足 V2.1 的 before / after / diff 语义。

---

# 3. Core Business Flow

## 3.1 Create Draft

```text
authenticated APPLICANT
        ↓
GET current MasterDataEntity
        ↓
before_data = deep copy current data_json
base_entity_version = entity.version
        ↓
apply top-level changes patch
        ↓
after_data
        ↓
compute deterministic diff
        ↓
reject no-op
        ↓
create ApprovalApplication(status=DRAFT)
        ↓
create MasterDataChange
        ↓
create ApprovalRecord(action=CREATE)
        ↓
commit
```

创建草稿不会修改当前 `MasterDataEntity.data_json`。

## 3.2 Update Draft

```text
authenticated APPLICANT
        ↓
load existing draft by draft_approval_id
        ↓
must be original applicant
        ↓
must still be DRAFT
        ↓
route entity identity must match draft
        ↓
apply new patch onto existing after snapshot
        ↓
recompute diff against original before_data
        ↓
reject no-op
        ↓
update draft
        ↓
ApprovalRecord(action=SAVE, DRAFT -> DRAFT)
        ↓
commit
```

更新草稿时：

- 原 `before_data` 不刷新。
- 原 `base_entity_version` 不刷新。
- 不自动 rebase 到最新主数据。
- 底层实体是否已经变化由后续 optimistic-lock TASK 处理。

---

# 4. Explicit Out of Scope

本 TASK 禁止实现：

- submit
- approve
- reject
- withdraw
- Workflow Instance
- current assignee resolution
- final approval 后写回主数据
- optimistic-lock enforcement
- Idempotency-Key
- audit_logs
- HITL
- Agent
- Tool
- LLM
- Knowledge / RAG
- 主数据 Schema 管理器
- 字段级业务枚举规则
- 部门 / 组织架构
- 删除字段语义
- JSON Patch RFC 6902
- nested path patch
- 前端页面
- P1-006 或后续任务

---

# 5. MasterDataEntity

新增 ORM：

```text
master_data_entities
```

建议：

```python
class MasterDataEntity(Base):
    """保存当前生效的主数据快照和版本。"""
```

字段：

| Field | Type | Rule |
|---|---|---|
| id | UUID | PK |
| entity_type | VARCHAR(128) | NOT NULL |
| entity_key | VARCHAR(128) | NOT NULL |
| data_json | JSONB | NOT NULL |
| version | INTEGER | NOT NULL, default 1 |
| created_at | TIMESTAMPTZ | NOT NULL |
| updated_at | TIMESTAMPTZ | NOT NULL |

约束：

```text
UNIQUE(entity_type, entity_key)
CHECK(version > 0)
```

命名建议：

```text
uq_master_data_entities_type_key
ck_master_data_entities_version_positive
```

---

# 6. Entity Identifier Rules

## 6.1 entity_type

使用稳定 lowercase snake_case：

```regex
^[a-z][a-z0-9_]{0,127}$
```

例如：

```text
supplier
customer
material
```

不自动 lowercase。

## 6.2 entity_key

entity_key 是业务稳定键。

要求：

- str。
- 长度 1～128。
- 不允许首尾空格。
- 不允许空字符串。
- 不自动 lowercase。
- 不把数据库 UUID 当业务 key。

允许：

```text
supplier_a
SUP-001
material:10001
```

一期不强制全部 snake_case。

---

# 7. JSON Data Contract

`MasterDataEntity.data_json` 必须是 JSON object。

禁止顶层为：

- list
- scalar
- null

例如合法：

```json
{
  "name": "Demo Supplier A",
  "payment_terms": "PREPAID",
  "currency": "CNY",
  "enabled": true
}
```

P1-005 不定义业务字段 schema。

因此：

- 不检查 payment_terms 是否属于某个业务枚举。
- 不检查某字段是否必填。
- 不判断某字段是否敏感。

这些属于后续 schema / validation 能力。

---

# 8. Change Patch Semantics

请求中的：

```text
changes
```

是 **top-level merge patch**，不是 JSON Patch。

例如当前：

```json
{
  "payment_terms": "PREPAID",
  "currency": "CNY"
}
```

请求：

```json
{
  "payment_terms": "NET_30"
}
```

after：

```json
{
  "payment_terms": "NET_30",
  "currency": "CNY"
}
```

一期规则：

- 只修改顶层 key。
- nested object 作为整体值替换。
- 不解析 `a.b.c`。
- 不支持 remove/delete sentinel。
- JSON null 是普通合法值。
- patch 不得为空。
- patch key 必须是非空 str，长度 <= 128。
- draft generation 不得原地修改 input dict 或 current entity data。

---

# 9. Diff Contract

新增纯领域 Diff 逻辑。

建议目录：

```text
backend/app/domain/master_data/
├── __init__.py
├── diff.py
└── errors.py
```

Diff item 固定结构：

```json
{
  "field": "payment_terms",
  "operation": "UPDATE",
  "before": "PREPAID",
  "after": "NET_30"
}
```

新增字段：

```json
{
  "field": "contact_phone",
  "operation": "ADD",
  "before": null,
  "after": "10086"
}
```

一期 Operation 只有：

```text
ADD
UPDATE
```

不实现 REMOVE。

## 9.1 Deterministic Ordering

Diff 必须按：

```text
field ascending
```

排序。

不得依赖请求 dict 插入顺序。

原因：

- API response 稳定。
- 测试稳定。
- UI Diff 稳定。
- Agent Trace 稳定。

## 9.2 No-op

如果 patch 应用后：

```text
before == after
```

或最终 Diff 为空：

拒绝创建 / 保存。

稳定错误码：

```text
MASTER_DATA_NO_CHANGE
```

---

# 10. Pure Domain API

建议纯函数：

```python
def apply_master_data_patch(
    base: dict[str, JsonValue],
    changes: dict[str, JsonValue],
) -> dict[str, JsonValue]:
    """在不修改输入对象的前提下生成完整 after snapshot。"""
```

以及：

```python
def build_master_data_diff(
    before: dict[str, JsonValue],
    after: dict[str, JsonValue],
) -> tuple[MasterDataDiffItem, ...]:
    """生成按字段稳定排序的结构化 Diff。"""
```

Diff domain 必须：

- 不依赖 FastAPI。
- 不依赖 SQLAlchemy。
- 不依赖 Repository。
- 不读数据库。

---

# 11. MasterDataChange Schema Evolution

现有：

```text
master_data_change
```

保留。

P1-005 migration 做：

## 11.1 Rename

```text
entity_id
→
entity_key
```

只重命名列，不改变现有值。

## 11.2 Add

新增：

```text
before_data JSONB NOT NULL
base_entity_version INTEGER NOT NULL
```

为了兼容可能存在的开发数据：

Migration 必须安全 backfill：

```text
before_data = form_data
base_entity_version = 1
```

然后再设置 NOT NULL。

不得直接新增无 default 的 NOT NULL 导致已有行升级失败。

新增：

```text
CHECK(base_entity_version > 0)
```

建议名称：

```text
ck_master_data_change_base_version_positive
```

## 11.3 Keep Existing Fields

继续保留：

```text
approval_id
entity_type
form_data
ext_data
change_items
change_reason
created_at
updated_at
```

P1-005 不删除 `ext_data`。

---

# 12. Why No FK from Change to MasterDataEntity Yet

P1-005 不新增：

```text
master_data_change → master_data_entities
```

数据库 FK。

Change 使用：

```text
entity_type + entity_key
```

作为业务身份快照。

理由：

1. P1-001 已经有历史结构。
2. 本 TASK 要保持 migration 对已有开发数据可升级。
3. 当前没有 Entity 删除能力。
4. Service 创建 Draft 时必须先确认实体存在。
5. P1-010 会继续强化并发 / 一致性约束。

这不是允许任意 orphan：

正常应用写路径必须通过 Service + Repository。

---

# 13. base_entity_version

创建 Draft 时：

```text
base_entity_version = current entity.version
```

更新 Draft 时：

```text
base_entity_version 保持不变
```

本 TASK 只保存这个 snapshot。

P1-005 不执行：

```text
WHERE version = base_entity_version
```

的 optimistic-lock enforcement。

真正的 stale-write 保护属于 P1-010。

---

# 14. Draft Storage

新 Draft 仍使用：

```text
ApprovalApplication(status=DRAFT)
+
MasterDataChange
```

不新增第二套：

```text
master_data_change_drafts
```

表。

原因：

- P1-001 已建立 ApprovalApplication 的 DRAFT 状态。
- P1-006 可以直接把同一 ApprovalApplication 从 DRAFT 提交为 IN_APPROVAL。
- 避免 Draft → Approval 再复制一次业务数据。

---

# 15. ApprovalApplication Mapping

新建主数据 Draft 时：

```text
business_type = MASTER_DATA_CHANGE
status = DRAFT
applicant_id = str(AuthContext.user_id)
business_id = str(MasterDataChange.id)
```

因为：

```text
business_id
```

是 ApprovalApplication 指向具体业务对象的通用引用。

P1-005 不修改 ApprovalApplication schema。

---

# 16. ApprovalRecord

## 16.1 Create Draft

创建时写：

```text
action = CREATE
from_status = null
to_status = DRAFT
operator_id = current authenticated user
```

CREATE 是审计事件，不调用 P1-002 状态迁移函数。

## 16.2 Update Draft

更新草稿时使用 P1-002：

```text
decide_transition(
    DRAFT,
    SAVE,
    TransitionContext(...)
)
```

验证：

- actor 必须是 applicant。
- 状态保持 DRAFT。

成功后写：

```text
ApprovalRecord(
  action=SAVE,
  from_status=DRAFT,
  to_status=DRAFT
)
```

不得复制一套新的 draft permission rule。

---

# 17. Draft Update Semantics

Update 由调用方显式传：

```text
draft_approval_id
```

不使用隐式：

```text
“同 applicant + entity 自动找最近一条草稿”
```

原因：

- 一个申请人未来可能对同一实体有多个草稿。
- 隐式最近草稿会造成歧义。
- Agent 后续可以通过 task state / recent approval 明确引用。

Update 要求：

1. ApprovalApplication 存在。
2. business_type == MASTER_DATA_CHANGE。
3. status == DRAFT。
4. applicant_id == current auth user。
5. 对应 MasterDataChange 存在。
6. route entity_type / entity_key 与 draft 一致。

否则稳定失败。

---

# 18. Iterative Draft Editing

更新草稿时：

```text
existing before_data
        ↓
保持不变

existing form_data(after snapshot)
        ↓
apply new patch
        ↓
new after snapshot

existing before_data
vs
new after snapshot
        ↓
recompute full diff
```

例如：

第一次：

```text
payment_terms:
PREPAID → NET_30
```

第二次 patch：

```text
currency:
CNY → USD
```

最终 Draft Diff 应包含两项。

如果第三次把：

```text
payment_terms
```

改回 PREPAID：

最终 Diff 只保留：

```text
currency:
CNY → USD
```

---

# 19. Reason

P1-005 的 create/update request：

```text
reason
```

必须：

- str。
- strip 后非空。
- 最大 2000 字符。

每次 update 都必须提供最终 reason。

不支持：

```text
reason=null 表示沿用旧 reason
```

保持 contract 明确。

---

# 20. Repository Boundaries

建议：

```text
backend/app/master_data/
├── __init__.py
├── repository.py
├── schemas.py
└── service.py
```

可按现有仓库风格等价调整。

## 20.1 MasterDataRepository

至少：

```text
get_entity(entity_type, entity_key)
add_entity(entity)
get_draft_by_approval_id(approval_id)
add_draft(...)
```

如拆分：

```text
MasterDataEntityRepository
MasterDataChangeRepository
```

也允许，但不要为了分层制造空类。

Repository：

- 使用调用方 AsyncSession。
- 不 commit。
- 不 rollback。
- 不依赖 FastAPI。
- 不构造 AuthContext。
- 不做 HTTP error mapping。

---

# 21. Service Boundary

建议：

```text
MasterDataService
```

至少：

```text
get_entity(...)
create_or_update_draft(...)
```

Service 负责：

- transaction boundary。
- trusted AuthContext。
- entity lookup。
- draft ownership。
- DRAFT 状态检查。
- patch / diff。
- ApprovalApplication 创建。
- ApprovalRecord CREATE / SAVE。
- MasterDataChange 创建 / 更新。

Service 不负责：

- submit。
- workflow selection。
- approver resolution。
- optimistic lock。
- idempotency。

---

# 22. Transaction Boundary

Create Draft 必须一个事务：

```text
load entity
+
create approval
+
create change
+
create CREATE record
```

任一步失败：

全部 rollback。

Update Draft 必须一个事务：

```text
load approval/change
+
validate SAVE
+
update change
+
create SAVE record
```

任一步失败：

全部 rollback。

需要 integration test 证明：

```text
flush 发生后注入异常
→ DB 无半成品
```

---

# 23. Auth / Permission

## 23.1 Read Entity

```text
GET master data
```

任何已认证用户可读。

使用：

```text
get_current_auth
```

只用于确认认证，不从 request 接收 user identity。

## 23.2 Create / Update Draft

必须：

```text
APPLICANT
```

使用：

```python
require_any_role(UserRole.APPLICANT)
```

`ADMIN` 不自动拥有 APPLICANT。

`applicant_id` 只能来自：

```text
AuthContext.user_id
```

禁止 request body / LLM / Tool args 提供 trusted applicant_id。

---

# 24. REST API

P1-005 实现两个 master-data API。

## 24.1 Read Entity

```http
GET /api/v1/master-data/{entity_type}/{entity_key}
Authorization: Bearer <token>
```

Response：

```json
{
  "id": "uuid",
  "entity_type": "supplier",
  "entity_key": "supplier_a",
  "data": {
    "name": "Demo Supplier A",
    "payment_terms": "PREPAID",
    "currency": "CNY",
    "enabled": true
  },
  "version": 1
}
```

## 24.2 Create / Update Change Draft

```http
POST /api/v1/master-data/{entity_type}/{entity_key}/changes
Authorization: Bearer <token>
```

Create：

```json
{
  "changes": {
    "payment_terms": "NET_30"
  },
  "reason": "与供应商重新约定账期"
}
```

Update：

```json
{
  "draft_approval_id": "uuid",
  "changes": {
    "currency": "USD"
  },
  "reason": "同时更新结算币种"
}
```

Response：

```json
{
  "approval_id": "uuid",
  "change_id": "uuid",
  "status": "DRAFT",
  "entity_type": "supplier",
  "entity_key": "supplier_a",
  "base_entity_version": 1,
  "before": {},
  "after": {},
  "diff": [],
  "reason": "..."
}
```

---

# 25. Error Model

新增 Master Data 应用错误码，必须进入现有统一 ErrorEnvelope。

建议：

| Code | HTTP | Meaning |
|---|---:|---|
| MASTER_DATA_NOT_FOUND | 404 | entity 不存在 |
| MASTER_DATA_INVALID_CHANGE | 422 | patch / identifier / reason 不合法 |
| MASTER_DATA_NO_CHANGE | 422 | after 与 before 无变化 |
| MASTER_DATA_DRAFT_NOT_FOUND | 404 | draft approval 不存在 |
| MASTER_DATA_DRAFT_NOT_EDITABLE | 409 | draft 已非 DRAFT |
| MASTER_DATA_DRAFT_FORBIDDEN | 403 | 不是原申请人 |
| MASTER_DATA_DRAFT_ENTITY_MISMATCH | 409 | route entity 与 draft 不一致 |

要求：

- 继续使用统一 ErrorEnvelope。
- request_id 必须保留。
- domain / repository 不抛 HTTPException。
- API boundary 才映射 HTTP status。

---

# 26. Demo Master Data Seed

新增：

```text
backend/scripts/seed_demo_master_data.py
```

固定 Demo：

```text
entity_type = supplier
entity_key = supplier_a
version = 1
```

初始 data：

```json
{
  "name": "Demo Supplier A",
  "payment_terms": "PREPAID",
  "currency": "CNY",
  "enabled": true
}
```

只使用虚构数据。

## 26.1 Production Protection

```text
APP_ENV=production
```

在 DB access 前拒绝 seed。

## 26.2 Idempotency

首次：

创建。

如果 `supplier/supplier_a` 已存在：

```text
no-op
```

不得把 data_json / version 强行重置成初始值。

原因：

以后审批完成后 Demo 主数据可能已经发生合法变化。

Seed 是：

```text
ensure demo entity exists
```

不是：

```text
reset demo database
```

---

# 27. Migration

新增 P1-005 migration。

父 revision：

```text
d1f004a203bc
```

Migration 只做：

1. 创建 `master_data_entities`。
2. rename `master_data_change.entity_id` → `entity_key`。
3. 增加 `before_data`。
4. backfill `before_data = form_data`。
5. `before_data` 改 NOT NULL。
6. 增加 `base_entity_version`。
7. backfill / default existing row version = 1。
8. 增加 `CHECK(base_entity_version > 0)`。
9. ORM metadata 与 migration 对齐。

不修改：

- ApprovalApplication schema。
- ApprovalRecord schema。
- auth tables。
- workflow_definitions。

---

# 28. Migration Downgrade

Downgrade 到：

```text
d1f004a203bc
```

必须：

- drop MasterDataEntity table。
- drop base version CHECK。
- drop `base_entity_version`。
- drop `before_data`。
- rename `entity_key` → `entity_id`。

并保留：

```text
approval_application
approval_record
master_data_change
users
user_roles
auth_sessions
workflow_definitions
```

需要真实 PostgreSQL smoke。

---

# 29. Data Snapshot Safety

Create Draft 时：

```text
before_data
form_data(after)
change_items
```

必须是独立 snapshot。

后续对任一 Python dict 的 mutation：

不得改变另外一个 snapshot。

测试必须证明：

```text
input current data
input changes
before
after
```

互不共享导致错误 mutation。

---

# 30. No-op Rules

以下都算 no-op：

```json
current:
{"currency":"CNY"}

changes:
{"currency":"CNY"}
```

以及 update 后所有字段最终都回到原值。

No-op：

- 不创建新 draft。
- 不修改 existing draft。
- 不新增 ApprovalRecord。
- 返回 `MASTER_DATA_NO_CHANGE`。

Update no-op 必须事务 rollback，保留之前 draft 内容不变。

---

# 31. Existing Current Entity Must Never Change During Draft

P1-005 的任何 Draft create/update 都必须保证：

```text
MasterDataEntity.data_json unchanged
MasterDataEntity.version unchanged
```

这是一条核心测试。

主数据真正写回只会在后续最终审批成功路径中实现。

---

# 32. P1-002 State Machine Reuse

Update DRAFT 时，不自己写：

```text
if status == DRAFT and current_user == applicant
```

作为唯一业务判断。

必须通过：

```text
decide_transition(
  current_status=DRAFT,
  action=SAVE,
  context=TransitionContext(...)
)
```

这样 P1-005 不复制状态机规则。

Create event 除外，因为 P1-002 已明确：

```text
CREATE is audit event, not state transition
```

---

# 33. P1-004 Workflow Definition Boundary

P1-005 不查询：

```text
supplier_change active workflow
```

也不绑定 workflow version。

原因：

```text
P1-005 = draft
P1-006 = submit + select/bind workflow
```

Workflow binding 留给 P1-006。

---

# 34. Required Tests

## 34.1 Pure Diff Domain

1. top-level UPDATE。
2. ADD。
3. nested object 整体替换。
4. null value 合法。
5. deterministic field ordering。
6. unchanged field 不进 diff。
7. empty patch 拒绝。
8. invalid field key 拒绝。
9. no-op 拒绝。
10. input objects 不被 mutation。
11. iterative patch 可保留旧改动。
12. 改回原值后对应 diff 消失。

## 34.2 MasterDataEntity ORM

13. unique(entity_type, entity_key)。
14. version > 0 CHECK。
15. JSONB roundtrip。
16. timezone timestamps。
17. data_json 只能通过应用正常写 object。
18. version default 1。

## 34.3 MasterDataChange ORM Evolution

19. entity_key 字段存在。
20. old entity_id 不存在。
21. before_data NOT NULL。
22. base_entity_version NOT NULL。
23. base version > 0 CHECK。
24. form_data 仍存在。
25. change_items 仍存在。
26. ext_data 保留。

## 34.4 Create Draft

27. APPLICANT 可创建。
28. 生成 ApprovalApplication DRAFT。
29. applicant_id 来自 AuthContext。
30. business_type MASTER_DATA_CHANGE。
31. business_id == change_id string。
32. before snapshot 正确。
33. after snapshot 正确。
34. diff 正确。
35. base_entity_version 正确。
36. CREATE ApprovalRecord 正确。
37. current MasterDataEntity 不变。
38. entity 不存在 → 404。
39. no-op 不产生任何 DB row。
40. create transaction 中途失败整体 rollback。

## 34.5 Update Draft

41. 原 applicant 可更新。
42. SAVE 使用 P1-002 状态机。
43. before snapshot 不变。
44. base_entity_version 不变。
45. patch 基于现有 after snapshot。
46. diff 按 original before 重算。
47. SAVE ApprovalRecord 写入。
48. 非 applicant → forbidden。
49. 非 DRAFT → not editable。
50. route entity mismatch → conflict。
51. unknown draft → not found。
52. update no-op 不修改已有 draft。
53. update transaction failure rollback。

## 34.6 API

54. 未认证 GET → 401。
55. 已认证 GET → 200。
56. APPLICANT create → 200/201。
57. APPROVER only create → 403。
58. ADMIN only create → 403。
59. response 不暴露敏感 auth 字段。
60. error envelope 带 request_id。
61. response diff 为结构化数据。

## 34.7 Seed

62. production before DB access 拒绝。
63. 首次创建 supplier_a。
64. 重复执行不重复。
65. existing entity 被修改后 seed 不 reset。
66. CLI 可运行。

## 34.8 Migration / Regression

67. upgrade head。
68. alembic check 无 drift。
69. downgrade 到 d1f004a203bc。
70. downgrade 后旧表保留。
71. master_data_change 恢复 entity_id。
72. re-upgrade head。
73. P1-001 tests 全绿。
74. P1-002 tests 全绿。
75. P1-003 tests 全绿。
76. P1-004 tests 全绿。
77. backend CI 全绿。
78. frontend CI 全绿。

---

# 35. CI Requirements

Backend CI 继续：

```text
uv sync --locked
ruff check
ruff format --check
alembic upgrade head
alembic check
pytest
```

增加 P1-005 migration smoke：

```text
downgrade d1f004a203bc
verify old tables / old master_data_change schema
upgrade head
```

Seed 顺序建议：

```text
seed_demo_users
seed_demo_workflows
seed_demo_master_data
```

不能删除已有 seed 检查。

---

# 36. Suggested Package Structure

允许等价调整。

```text
backend/app/
├── api/
│   └── v1/
│       └── master_data.py
├── domain/
│   └── master_data/
│       ├── __init__.py
│       ├── diff.py
│       └── errors.py
├── master_data/
│   ├── __init__.py
│   ├── errors.py
│   ├── repository.py
│   ├── schemas.py
│   └── service.py
└── models/
    └── master_data.py

backend/scripts/
├── seed_demo_master_data.py
└── verify_p1_005_downgrade.py

backend/tests/
├── master_data/
│   ├── test_diff.py
│   ├── test_master_data_api.py
│   ├── test_master_data_service.py
│   └── test_seed_demo_master_data.py
└── test_master_data_persistence.py
```

---

# 37. Error Handler Integration

现有 `app_error_handler` 已支持 Auth。

P1-005 可以扩展 HTTP code mapping。

要求：

- 不破坏 Auth mapping。
- 未知 AppError 不被错误映射成 2xx。
- Master Data error code 有稳定状态码。
- 401 `WWW-Authenticate` 行为仍只用于 auth 401。

如现有 error handler 结构开始变得难维护，可以做最小重构为：

```text
ERROR_HTTP_STATUS
```

集中 map。

禁止引入复杂 exception framework。

---

# 38. Comments / Docstrings

继续：

- 所有函数 / 方法中文 docstring。
- ORM / dataclass / Enum / error class 中文 docstring。
- test function 一行中文 docstring。
- 注释解释 why。

关键 why：

1. 为什么 Draft 不修改 current master data。
2. 为什么 before/base version 创建后保持不变。
3. 为什么 update patch 叠加在 existing after。
4. 为什么 diff 每次从 original before 重算。
5. 为什么不自动找“最近草稿”。
6. 为什么 Demo seed 不 reset 已存在实体。
7. 为什么 P1-005 不绑定 workflow。
8. 为什么 optimistic-lock enforcement 留给 P1-010。

---

# 39. Security / Trust Boundaries

必须：

- applicant identity 只来自 AuthContext。
- role 只来自 DB AuthContext。
- request 不接收可信 applicant_id。
- ADMIN 不隐式 bypass APPLICANT。
- 只允许原申请人修改 draft。
- 不输出 password/token/hash。
- 不使用真实公司主数据。
- 不把 LLM 生成内容直接写 DB 而跳过 service validation。

---

# 40. Design Decisions Frozen

1. 新增 MasterDataEntity。
2. current data 使用 JSONB snapshot。
3. MasterDataEntity version 从 1 开始。
4. Draft 仍使用 ApprovalApplication DRAFT + MasterDataChange。
5. 不新增第二张 draft table。
6. entity_id 重命名 entity_key。
7. 新增 before_data。
8. form_data = complete after snapshot。
9. change_items = structured diff。
10. 新增 base_entity_version。
11. ext_data 保留但本 TASK 不使用。
12. patch 是 top-level merge patch。
13. 不支持 remove。
14. nested object 整体替换。
15. diff operation 只有 ADD / UPDATE。
16. diff 按 field 稳定排序。
17. no-op 拒绝。
18. Draft 不修改 current MasterDataEntity。
19. create draft 同时创建 ApprovalApplication。
20. business_id = change_id string。
21. CREATE 写 ApprovalRecord。
22. update 写 SAVE record 并复用 P1-002 state machine。
23. update 显式使用 draft_approval_id。
24. before/base version 在 Draft 生命周期内固定。
25. update patch 基于现有 after。
26. final diff 对 original before 重算。
27. P1-005 不绑定 Workflow Definition。
28. P1-005 不 enforce optimistic lock。
29. create/update 只允许 APPLICANT。
30. 实现 GET entity 与 POST changes API。
31. Demo entity = supplier / supplier_a。
32. Demo seed 不 reset existing entity。
33. Migration parent = d1f004a203bc。

任何冻结项需要改变：

停止并提交 Architecture Deviation，不得自行修改。

---

# 41. Definition of Done

- [ ] MasterDataEntity ORM
- [ ] unique(type,key)
- [ ] version positive CHECK
- [ ] JSONB current snapshot
- [ ] pure patch logic
- [ ] pure deterministic diff
- [ ] no-op detection
- [ ] entity_id renamed entity_key
- [ ] before_data
- [ ] base_entity_version
- [ ] safe migration backfill
- [ ] Create Draft transaction
- [ ] ApprovalApplication DRAFT
- [ ] CREATE ApprovalRecord
- [ ] Update Draft transaction
- [ ] P1-002 SAVE reuse
- [ ] ownership check
- [ ] Draft route identity check
- [ ] current entity unchanged
- [ ] GET master data API
- [ ] POST draft API
- [ ] AuthContext identity
- [ ] APPLICANT guard
- [ ] ErrorEnvelope integration
- [ ] Demo master data seed
- [ ] production seed protection
- [ ] seed does not reset current data
- [ ] upgrade / downgrade / re-upgrade
- [ ] alembic check no drift
- [ ] previous Phase regression tests green
- [ ] backend CI green
- [ ] frontend CI green
- [ ] no P1-006 implementation

---

# 42. Completion Report

Codex 最终必须输出：

```text
TASK Completion Report

TASK ID:
TASK-P1-005

Status:
READY_FOR_REVIEW

Changed files:

MasterDataEntity:

MasterDataChange schema evolution:

Patch semantics:

Diff semantics:

No-op behavior:

Draft create transaction:

Draft update transaction:

ApprovalApplication mapping:

ApprovalRecord CREATE/SAVE:

P1-002 reuse:

Auth / permission:

REST API:

Error mapping:

Demo master data seed:

Migration:

Migration parent:

Upgrade verification:

Downgrade verification:

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

不得自行写 DONE。

---

# 43. Codex Pre-Implementation Plan Prompt

```text
当前进入 TASK-P1-005 — MasterDataEntity + Change Draft / Diff。

本轮不要编码。

Source of Truth：

- CODEX.md
- docs/V2.1-technical-design.md
- docs/V3.0-codex-execution-manual.md
- docs/tasks/TASK-P1-005.md

其中 P1-005 具体范围，以：

docs/tasks/TASK-P1-005.md

为最高优先级。

开始前执行：

git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short

预期：

branch:
task/p1-005

HEAD 必须包含已合并 P1-004 main：
31353d749d09bd14d7d7301add441e7ef558977b

工作区必须干净。

如果不满足：

停止。
不要 reset --hard。
不要自行 merge / rebase。
报告实际状态。

然后完整阅读：

- CODEX.md
- docs/V2.1-technical-design.md
- docs/V3.0-codex-execution-manual.md
- docs/tasks/TASK-P1-005.md
- backend/app/domain/**
- backend/app/models/**
- backend/app/auth/**
- backend/app/workflow/**
- backend/app/api/**
- backend/app/infrastructure/database.py
- backend/alembic/**
- backend/scripts/**
- backend/tests/**
- .github/workflows/ci.yml

本轮只输出：

# Pre-Implementation Plan — TASK-P1-005

至少覆盖：

1. TASK 目标与边界
2. 新增 / 修改文件
3. MasterDataEntity ORM
4. entity_type / entity_key validation
5. data_json contract
6. version constraint
7. MasterDataChange schema evolution
8. entity_id -> entity_key migration
9. before_data backfill
10. base_entity_version
11. form_data / change_items / ext_data 现有字段语义
12. top-level patch semantics
13. JSON null / nested object 行为
14. no delete semantics
15. deterministic Diff structure
16. ADD / UPDATE 判断
17. no-op detection
18. input snapshot immutability
19. Create Draft transaction
20. ApprovalApplication DRAFT mapping
21. business_id = change_id
22. CREATE ApprovalRecord
23. Update Draft flow
24. explicit draft_approval_id
25. applicant ownership
26. route entity mismatch
27. P1-002 SAVE state machine reuse
28. original before/base version preservation
29. iterative patch onto existing after
30. full diff recomputation
31. current entity 不被修改
32. MasterData Repository
33. MasterData Service
34. transaction ownership
35. AuthContext / APPLICANT permission
36. GET master data API
37. POST changes API
38. ErrorEnvelope mapping
39. Demo supplier_a seed
40. seed create-only / no reset behavior
41. Migration parent
42. downgrade / re-upgrade
43. PostgreSQL integration tests
44. transaction rollback tests
45. regression tests
46. CI adjustments
47. 如何保证不绑定 Workflow
48. 如何保证不进入 optimistic lock / submit
49. Architecture risks / reviewer questions
50. Out of Scope

不要编码。
不要生成 migration。
不要安装依赖。
不要修改文件。

等待 Architecture Review APPROVE。
```
