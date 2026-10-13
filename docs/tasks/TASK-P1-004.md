# TASK-P1-004 — Workflow Definition

## 0. Status

```text
TASK ID: P1-004
Name: Workflow Definition
Phase: Phase 1 — Approval Backend
Status: DESIGN FROZEN / WAITING FOR PRE-IMPLEMENTATION PLAN
```

本文件是 TASK-P1-004 的具体执行 Source of Truth。

若本文件与 V2.1 / V3.0 中历史性的 P1 TASK 编号、旧状态名或概念示例发生直接冲突，以本文件和已经合并的 P1-001 / P1-002 / P1-003 实现为准。

本 TASK 开始基线：

```text
main:
2d510596f5496d9284f4b5cf9fc040503ad5ee3f

P1-001:
Approval Domain Models

P1-002:
Approval State Machine

P1-003:
Demo Users / Authentication / Roles
```

P1-004 不重新设计前三个 TASK。

---

# 1. Goal

建立轻量、版本化、可持久化、可验证、可导航的 Workflow Definition（流程定义）能力，为后续：

- P1-006 Submit Transaction
- P1-007 Approve / Reject
- P1-008 Withdraw

提供稳定的“流程模板”。

本 TASK 只回答：

```text
一个审批流程定义长什么样？
有哪些审批节点？
节点顺序是什么？
每个节点要求什么角色？
当前启用的是哪个版本？
给定节点的前一节点 / 后一节点是什么？
```

本 TASK **不运行审批实例**。

---

# 2. Core Concept

Workflow Definition 是“流程模板”，不是“流程实例”。

例如：

```text
supplier_change v1

manager_review
    APPROVER
        ↓
data_admin_review
    DATA_ADMIN
```

定义表示：

1. 第一个节点为 `manager_review`。
2. 该节点要求 `APPROVER` 角色。
3. 下一节点为 `data_admin_review`。
4. 最终节点要求 `DATA_ADMIN` 角色。

它不表示：

- manager 这个 Demo 用户一定是审批人；
- data_admin 这个 Demo 用户一定是最终审批人；
- 当前已经存在真实审批实例；
- 当前已经有人收到待办。

具体审批人 / assignee 解析属于后续 Approval Service / Workflow Runtime 范围。

---

# 3. Relationship to Existing Domain

## 3.1 与 P1-002 State Machine 的关系

P1-002 已经使用：

```text
is_first_approval_node
is_final_approval_node
current_assignee_ids
```

P1-004 提供前两类流程结构信息：

```text
Workflow Definition
        ↓
current node position
        ↓
is_first_approval_node
is_final_approval_node
```

但 P1-004 不负责解析：

```text
current_assignee_ids
```

具体审批人属于后续任务。

## 3.2 与 P1-003 UserRole 的关系

节点角色必须复用唯一的：

```python
app.domain.auth.enums.UserRole
```

禁止重新定义：

```text
WorkflowRole
ApprovalNodeRole
ManagerRole
```

等重复 Enum。

一期审批节点只允许：

```text
APPROVER
DATA_ADMIN
```

禁止把：

```text
APPLICANT
ADMIN
```

作为审批节点要求角色。

特别说明：

```text
ADMIN != business superuser
```

P1-003 的该规则继续成立。

## 3.3 Workflow Code 与 BusinessType 不等价

当前 Approval Domain 有：

```text
BusinessType.MASTER_DATA_CHANGE
```

Workflow Definition 示例使用：

```text
supplier_change
```

两者概念不同：

```text
BusinessType
= 业务申请类别

Workflow code
= 某套审批流程模板稳定标识
```

P1-004 不新增二者之间的绑定表，也不修改 ApprovalApplication。

---

# 4. Scope

本 TASK 实现：

1. Workflow Definition 纯领域模型。
2. Workflow Node 纯领域模型。
3. Workflow Definition validation。
4. Workflow node navigation。
5. `workflow_definitions` ORM。
6. P1-004 Alembic migration。
7. Active Version 数据库约束。
8. Workflow Definition Repository。
9. Domain JSON codec / mapping。
10. Definition version immutability 规则。
11. Demo Workflow Seed。
12. PostgreSQL integration tests。
13. Migration / schema drift tests。
14. CI 中的 Workflow Seed 验证。

---

# 5. Explicit Out of Scope

本 TASK 禁止实现：

- BPMN
- BPMN XML
- Workflow designer
- 可视化流程编辑器
- 条件分支
- Gateway
- 并行审批
- 会签 / 或签
- Timer
- Event
- Script node
- Expression engine
- 动态条件 DSL
- Workflow REST 管理 API
- Workflow 管理后台
- Workflow Instance
- Task Instance
- ApprovalApplication 提交
- ApprovalApplication 状态迁移
- ApprovalApplication schema 修改
- ApprovalRecord schema 修改
- current_assignee_ids 解析
- 组织架构
- 部门经理解析
- 指定真实审批人
- submit / approve / reject / withdraw API
- Tool
- ToolExecutor
- Agent
- LLM
- HITL
- 前端
- P1-005 / P1-006 的任何业务实现

---

# 6. Workflow Definition JSON Contract

一期固定使用轻量 JSON Definition。

Demo 定义：

```json
{
  "code": "supplier_change",
  "version": 1,
  "nodes": [
    {
      "key": "manager_review",
      "role": "APPROVER"
    },
    {
      "key": "data_admin_review",
      "role": "DATA_ADMIN"
    }
  ]
}
```

JSON 中保留：

```text
code
version
nodes
```

数据库表同时保存 `code` 和 `version` 作为可索引元数据。

要求：

```text
row.code == definition_json.code
row.version == definition_json.version
```

Repository / codec 写入前必须保证一致。

不得出现：

```text
row.code = supplier_change
definition_json.code = purchase_contract
```

---

# 7. Stable Identifier Convention

Workflow code 和 Node key 是机器稳定标识，不是展示名称。

本项目一期统一采用 lowercase snake_case。

合法示例：

```text
supplier_change
manager_review
data_admin_review
a1_review
```

建议固定校验：

```regex
^[a-z][a-z0-9_]{0,127}$
```

即：

- 长度 1～128。
- 必须以小写字母开始。
- 后续仅允许小写字母、数字、下划线。
- 不自动 lowercase。
- 不自动修正非法 code。
- 校验失败直接拒绝。

原因：

- 防止 `Supplier_Change` / `supplier_change` 成为两个不同流程。
- code/key 是持久化标识，应由定义者明确提供。
- 展示名称以后可以单独增加，不混入机器 key。

---

# 8. Domain Types

建议目录：

```text
backend/app/domain/workflow/
├── __init__.py
├── definition.py
└── errors.py
```

允许在不改变边界的前提下做等价文件拆分。

## 8.1 WorkflowNodeDefinition

建议：

```python
@dataclass(frozen=True)
class WorkflowNodeDefinition:
    """表示流程定义中的一个不可变审批节点。"""

    key: str
    role: UserRole
```

要求：

- frozen。
- 不保存具体 user_id。
- 不保存 username。
- 不保存 current assignee。
- role 必须来自 P1-003 `UserRole`。

## 8.2 WorkflowDefinitionSpec

建议：

```python
@dataclass(frozen=True)
class WorkflowDefinitionSpec:
    """表示一个不可变、已验证的流程定义版本。"""

    code: str
    version: int
    nodes: tuple[WorkflowNodeDefinition, ...]
```

要求：

- Domain 对象不可变。
- nodes 使用 tuple，不使用可原地修改的 list。
- Domain 不依赖 SQLAlchemy、FastAPI、Redis。

---

# 9. Domain Validation Rules

一个 WorkflowDefinitionSpec 只有满足全部规则才合法。

## 9.1 code

必须：

- 非空。
- 满足稳定标识 regex。
- 长度 <= 128。

错误码建议：

```text
WORKFLOW_DEFINITION_INVALID_CODE
```

## 9.2 version

必须：

```text
version > 0
```

禁止：

- 0
- 负数
- bool 冒充 int

错误码：

```text
WORKFLOW_DEFINITION_INVALID_VERSION
```

## 9.3 nodes

必须至少 1 个节点。

禁止：

```json
{
  "nodes": []
}
```

错误码：

```text
WORKFLOW_DEFINITION_NODES_EMPTY
```

## 9.4 node.key

每个 key：

- 必须合法。
- 同一 workflow version 内必须唯一。

禁止：

```json
{
  "nodes": [
    {"key": "review", "role": "APPROVER"},
    {"key": "review", "role": "DATA_ADMIN"}
  ]
}
```

错误码建议：

```text
WORKFLOW_NODE_INVALID_KEY
WORKFLOW_NODE_DUPLICATE_KEY
```

## 9.5 node.role

输入 JSON role 必须能解析成 P1-003：

```python
UserRole
```

非法字符串例如：

```text
MANAGER
SUPER_ADMIN
UNKNOWN
```

必须拒绝。

即使 role 是合法 UserRole，审批节点一期仍只允许：

```text
APPROVER
DATA_ADMIN
```

因此：

```text
APPLICANT
ADMIN
```

也必须拒绝作为 Workflow Node role。

建议错误码：

```text
WORKFLOW_NODE_INVALID_ROLE
WORKFLOW_NODE_ROLE_NOT_ALLOWED
```

---

# 10. Domain Error Model

建议：

```python
class WorkflowDefinitionErrorCode(StrEnum):
    """定义流程模板解析、校验和导航的稳定错误码。"""

    INVALID_CODE = "WORKFLOW_DEFINITION_INVALID_CODE"
    INVALID_VERSION = "WORKFLOW_DEFINITION_INVALID_VERSION"
    NODES_EMPTY = "WORKFLOW_DEFINITION_NODES_EMPTY"
    NODE_INVALID_KEY = "WORKFLOW_NODE_INVALID_KEY"
    NODE_DUPLICATE_KEY = "WORKFLOW_NODE_DUPLICATE_KEY"
    NODE_INVALID_ROLE = "WORKFLOW_NODE_INVALID_ROLE"
    NODE_ROLE_NOT_ALLOWED = "WORKFLOW_NODE_ROLE_NOT_ALLOWED"
    NODE_NOT_FOUND = "WORKFLOW_NODE_NOT_FOUND"
    PAYLOAD_INVALID = "WORKFLOW_DEFINITION_PAYLOAD_INVALID"
```

并提供纯领域异常：

```python
class WorkflowDefinitionError(Exception):
    """携带稳定错误码的流程定义领域异常。"""
```

Domain error：

- 不继承 FastAPI HTTPException。
- 不映射 HTTP status。
- 不依赖 `AppError`。
- 不依赖 ORM。

本 TASK 没有 Workflow REST API，因此不需要建立 Workflow HTTP error mapping。

---

# 11. JSON Codec

需要提供明确的：

```text
JSON payload
↔
WorkflowDefinitionSpec
```

转换边界。

允许命名：

```text
parse_workflow_definition
workflow_definition_to_dict
```

或等价命名。

## 11.1 Parse

输入：

```python
dict[str, object]
```

输出：

```python
WorkflowDefinitionSpec
```

必须：

1. 校验顶层结构。
2. 读取 code。
3. 读取 version。
4. 读取 nodes。
5. 校验每个 node 的 key / role。
6. 拒绝重复 node key。
7. 返回 frozen Domain object。

一期建议对 JSON 结构采用 strict contract：

顶层只允许：

```text
code
version
nodes
```

node 只允许：

```text
key
role
```

出现未知字段时返回：

```text
WORKFLOW_DEFINITION_PAYLOAD_INVALID
```

避免拼写错误被静默忽略。

## 11.2 Serialize

序列化结果必须稳定：

```json
{
  "code": "...",
  "version": 1,
  "nodes": [
    {"key": "...", "role": "APPROVER"}
  ]
}
```

role 使用：

```text
role.value
```

不得序列化 Python Enum repr。

---

# 12. Node Navigation

WorkflowDefinitionSpec 至少提供等价能力：

```text
first_node()
final_node()
get_node(key)
next_node(key)
previous_node(key)
is_first_node(key)
is_final_node(key)
```

## 12.1 first_node

返回：

```text
nodes[0]
```

## 12.2 final_node

返回：

```text
nodes[-1]
```

## 12.3 get_node

存在：

返回对应 node。

不存在：

抛：

```text
WORKFLOW_NODE_NOT_FOUND
```

不得返回错误节点或默认首节点。

## 12.4 next_node

当前节点不是最终节点：

返回下一节点。

最终节点：

```python
None
```

未知 key：

抛 `WORKFLOW_NODE_NOT_FOUND`。

## 12.5 previous_node

当前节点不是首节点：

返回前一节点。

首节点：

```python
None
```

未知 key：

抛 `WORKFLOW_NODE_NOT_FOUND`。

## 12.6 first / final flags

例如两节点流程：

```text
manager_review:
first = true
final = false

data_admin_review:
first = false
final = true
```

单节点流程必须合法：

```text
single_review:
first = true
final = true
previous = None
next = None
```

该语义必须兼容 P1-002。

---

# 13. Persistence Model

新增：

```text
workflow_definitions
```

建议 ORM：

```python
class WorkflowDefinition(Base):
    """保存不可变流程版本及其当前启用状态。"""
```

字段：

| Field | Type | Rule |
|---|---|---|
| id | UUID | PK，应用生成 |
| code | VARCHAR(128) | NOT NULL |
| version | INTEGER | NOT NULL |
| definition_json | JSONB | NOT NULL |
| active | BOOLEAN | NOT NULL，default false |
| created_at | TIMESTAMPTZ | NOT NULL |
| updated_at | TIMESTAMPTZ | NOT NULL |

## 13.1 UUID

内部主键：

```text
UUID
```

Workflow 业务身份：

```text
(code, version)
```

调用方不要依赖 UUID 代表版本号。

## 13.2 definition_json

使用 PostgreSQL：

```text
JSONB
```

原因：

- Workflow Definition 是小型、版本化配置快照。
- 当前 nodes 结构很轻。
- 一期不值得拆出 workflow_nodes 表。
- JSONB 可完整保留某个版本的结构。

要求：

- NOT NULL。
- 不使用共享 mutable default。
- 不使用 MutableDict / MutableList 来鼓励原地修改。
- 通过 codec 生成标准 payload。

---

# 14. Database Constraints

## 14.1 Version Positive

数据库增加：

```text
CHECK(version > 0)
```

具名建议：

```text
ck_workflow_definitions_version_positive
```

## 14.2 Unique Version

同一 code 的同一 version 只能存在一次：

```text
UNIQUE(code, version)
```

具名：

```text
uq_workflow_definitions_code_version
```

合法：

```text
supplier_change v1
supplier_change v2
```

非法：

```text
supplier_change v1
supplier_change v1
```

## 14.3 Single Active Version Per Code

同一 code 最多一个：

```text
active = true
```

必须由 PostgreSQL 数据库约束保证。

推荐 partial unique index：

```sql
UNIQUE(code) WHERE active = true
```

SQLAlchemy / Alembic 可使用等价：

```text
postgresql_where = active
```

索引具名：

```text
uq_workflow_definitions_active_code
```

合法：

```text
supplier_change v1 active=false
supplier_change v2 active=true
```

也合法：

```text
supplier_change v1 active=false
supplier_change v2 active=false
```

非法：

```text
supplier_change v1 active=true
supplier_change v2 active=true
```

“最多一个 active”不等于“永远必须有一个 active”。

是否必须存在 active version 由后续业务提交时决定，不通过数据库强制。

---

# 15. Version Immutability

这是 P1-004 最重要的业务规则之一。

Workflow Definition 一旦创建：

```text
code
version
definition_json
```

视为一个不可变版本。

例如已存在：

```text
supplier_change v1

manager_review
→ data_admin_review
```

如果以后要增加：

```text
finance_review
```

禁止：

```text
UPDATE supplier_change v1 definition_json
```

必须创建：

```text
supplier_change v2
```

原因：

后续运行中的审批实例可能已经绑定 v1。

若 v1 被原地修改，将无法回答：

```text
“这个历史审批当时到底按哪套流程执行？”
```

## 15.1 What May Change

同一个 WorkflowDefinition row 中只有：

```text
active
updated_at
```

允许因版本启停发生变化。

不可变：

```text
code
version
definition_json
```

## 15.2 Enforcement Level

P1-004 不引入 PostgreSQL trigger。

但必须通过以下方式保护：

1. Domain Spec frozen。
2. Repository 不提供修改 code/version/definition_json 的 API。
3. Seed 遇到相同 `code + version` 但 payload 不一致时明确失败。
4. Tests 明确覆盖 immutable conflict。
5. Code comments 说明版本变更必须创建新 version。

禁止为了“幂等 seed”直接覆盖已存在版本的 definition_json。

---

# 16. Active Version Semantics

`active` 表示：

```text
当前新的业务提交应优先选择的流程版本
```

不表示：

```text
旧版本无效
旧审批实例立即迁移
旧版本可以删除
```

例如：

```text
supplier_change v1 active=false
supplier_change v2 active=true
```

已经基于 v1 启动的审批未来仍必须按 v1 解释。

P1-004 不实现运行实例，但数据模型必须保留这个语义。

---

# 17. Repository Boundary

建议：

```text
backend/app/workflow/
├── __init__.py
└── repository.py
```

Repository 至少提供等价能力：

```text
get_by_code_version(code, version)
get_active(code)
add(definition)
```

可按实现需要增加：

```text
list_versions(code)
```

但不要增加本 TASK 不需要的大量 CRUD。

Repository：

- 接收 AsyncSession。
- 不创建全局 Session。
- 不 commit。
- 不 rollback。
- 可以 add / execute / flush。
- 不依赖 FastAPI。
- 不处理 HTTP。
- 不处理 Agent / Tool。
- 不根据 username 分配审批人。

事务由调用方 / application layer / seed 持有。

---

# 18. Domain / ORM Mapping

Repository 或独立 mapper 必须保证：

```text
WorkflowDefinition.definition_json
        ↓ parse
WorkflowDefinitionSpec
```

以及：

```text
WorkflowDefinitionSpec
        ↓ serialize
definition_json
```

写入 ORM 时：

```text
row.code
row.version
definition_json.code
definition_json.version
```

必须一致。

不要让数据库 row 和 JSON payload 成为两个互相矛盾的事实来源。

---

# 19. No Workflow Service Requirement Yet

本 TASK 没有 HTTP API，也没有真实提交审批动作。

因此不强制为了“分层完整”创建空洞的：

```text
WorkflowService
WorkflowManager
WorkflowEngine
```

如果实现确实需要一个很小的 application service 来承载：

- create version
- active switch

可以在 Pre-Implementation Plan 中说明必要性，再由 Reviewer 决定。

默认优先：

```text
Domain
+
Repository
+
Seed/Application Transaction
```

完成本 TASK。

---

# 20. Demo Workflow Seed

新增独立脚本：

```text
backend/scripts/seed_demo_workflows.py
```

固定 CLI：

```bash
cd backend
uv run python -m scripts.seed_demo_workflows
```

## 20.1 Demo Definition

Demo Seed 使用：

```json
{
  "code": "supplier_change",
  "version": 1,
  "nodes": [
    {
      "key": "manager_review",
      "role": "APPROVER"
    },
    {
      "key": "data_admin_review",
      "role": "DATA_ADMIN"
    }
  ]
}
```

初次创建：

```text
active = true
```

## 20.2 Production Protection

若：

```text
APP_ENV=production
```

必须在数据库访问前拒绝运行。

与 Demo User Seed 保持一致的安全原则。

## 20.3 Seed Idempotency

首次执行：

```text
创建 supplier_change v1
active=true
```

再次执行，如果 v1 的 immutable payload 完全一致：

```text
不新增重复版本
不覆盖 definition_json
```

## 20.4 Immutable Conflict

如果数据库中已有：

```text
supplier_change v1
```

但 definition_json 与 Demo v1 不一致：

必须失败。

建议稳定错误 / RuntimeError 信息明确表达：

```text
existing workflow version differs; create a new version instead
```

禁止：

```text
UPDATE definition_json
```

## 20.5 Existing Newer Version

为了避免旧 seed 意外把生产式配置“降级”回 v1：

如果：

```text
supplier_change v2
```

已经存在且 v1 不存在：

Seed 不得偷偷插入 retroactive v1 并激活。

应明确失败并提示 workflow history 已存在。

如果：

```text
v1 已存在且 payload 一致
v2 active=true
v1 active=false
```

再次执行 v1 Seed：

- 保留 v2 active=true。
- 保留 v1 active=false。
- 不把 v1 重新激活。
- 不修改 v2。

如果：

```text
v1 已存在且 payload 一致
没有任何 active version
```

允许 Demo Seed 将 v1 重新设为 active=true，以恢复 Demo 可用状态。

所有 Seed 写操作在单个事务内完成。

---

# 21. Delete Policy

P1-004 不提供 Workflow Definition 删除功能。

原因：

- 已运行实例未来可能依赖历史版本。
- 删除历史版本会破坏审计与解释能力。

因此：

```text
no delete API
no cascade cleanup
```

本 TASK 不实现历史 Workflow GC。

---

# 22. No ApprovalApplication Schema Change

P1-004 明确禁止修改：

```text
approval_application
approval_record
master_data_change
```

特别禁止提前加入：

```text
workflow_definition_id
workflow_code
workflow_version
```

到 ApprovalApplication。

这些字段如何绑定审批实例，在 P1-006 Submit Transaction 中结合真实事务一起决定。

这样保证：

```text
P1-004 = 定义模板
P1-006 = 运行 / 绑定模板
```

职责不混淆。

---

# 23. Alembic

新增 P1-004 migration。

父 revision 必须为：

```text
b15a8d09c2f1
```

即 P1-003 Auth migration。

Migration 只新增：

```text
workflow_definitions
```

要求：

- 不修改 P1-001 表。
- 不修改 P1-003 auth 表。
- 不写 Demo workflow seed。
- 建立 version > 0 CHECK。
- 建立 (code, version) UNIQUE。
- 建立 active partial unique index。
- JSONB NOT NULL。
- active NOT NULL。
- migration 与 ORM metadata 一致。

Downgrade 到：

```text
b15a8d09c2f1
```

后：

必须仍保留：

```text
approval_application
approval_record
master_data_change
users
user_roles
auth_sessions
```

只删除：

```text
workflow_definitions
```

---

# 24. Required Tests

以下为 P1-004 最低测试矩阵。

## 24.1 Domain Validation

1. 合法两节点 workflow 可创建。
2. 合法单节点 workflow 可创建。
3. code 为空拒绝。
4. code 不符合 snake_case 规则拒绝。
5. version=0 拒绝。
6. version<0 拒绝。
7. bool version 拒绝。
8. nodes 为空拒绝。
9. node key 非法拒绝。
10. duplicate node key 拒绝。
11. unknown role string 拒绝。
12. APPLICANT 节点拒绝。
13. ADMIN 节点拒绝。
14. APPROVER 节点允许。
15. DATA_ADMIN 节点允许。
16. payload 缺字段拒绝。
17. payload 未知顶层字段拒绝。
18. node 未知字段拒绝。
19. serialize → parse round trip 一致。

## 24.2 Navigation

20. first_node 正确。
21. final_node 正确。
22. get_node 正确。
23. unknown node → WORKFLOW_NODE_NOT_FOUND。
24. 首节点 previous_node → None。
25. 中间节点 previous/next 正确。
26. 最终节点 next_node → None。
27. is_first_node 正确。
28. is_final_node 正确。
29. 单节点同时 first/final。
30. 单节点 previous/next 都为 None。

## 24.3 Domain Architecture

31. `app.domain.workflow` 不 import FastAPI。
32. 不 import SQLAlchemy。
33. 不 import app.models。
34. 不 import app.infrastructure。
35. 节点 role 复用唯一 UserRole，而不是重新定义。

## 24.4 Persistence

36. workflow_definitions 可正常持久化。
37. code/version 与 definition_json 一致。
38. definition_json JSONB round trip 正确。
39. timezone-aware created_at/updated_at。
40. version <= 0 被 DB CHECK 拒绝。
41. duplicate (code, version) 被 DB UNIQUE 拒绝。
42. 同 code 两个 active=true 被 partial UNIQUE 拒绝。
43. 同 code 两个 inactive 版本允许。
44. 不同 code 各自 active=true 允许。

## 24.5 Repository

45. get_by_code_version 命中正确版本。
46. 不存在版本返回 None。
47. get_active 返回唯一 active version。
48. 无 active version 返回 None。
49. Repository add/flush 不隐式 commit。

## 24.6 Immutability

50. 相同 code/version + 完全相同 payload 视为相同版本。
51. 相同 code/version + 不同 nodes 不允许被 Seed 覆盖。
52. 不提供 Repository update_definition API。
53. active 可以切换而不修改 immutable payload。
54. v1 / v2 payload 能同时保留。

## 24.7 Seed

55. production 在 DB access 前拒绝。
56. 首次执行创建 supplier_change v1。
57. 初次 v1 active=true。
58. 重复执行不产生 duplicate version。
59. 重复执行不修改一致 payload。
60. existing v1 payload 不一致时失败。
61. v2 active / v1 inactive 时重复 v1 seed 不降级 active version。
62. v1 一致且没有 active version 时可恢复 v1 active。
63. v1 缺失但已有其他版本时 seed 拒绝 retroactive insertion。
64. Seed 事务失败整体 rollback。
65. 固定 module CLI 可运行。

## 24.8 Migration / Regression

66. `alembic upgrade head` 成功。
67. `alembic check` 无 drift。
68. downgrade P1-004 到 `b15a8d09c2f1` 成功。
69. downgrade 后 P1-001 / P1-003 表仍存在。
70. 再次 upgrade head 成功。
71. P1-001 tests 全绿。
72. P1-002 tests 全绿。
73. P1-003 Auth tests 全绿。
74. backend CI 全绿。
75. frontend CI 全绿。

---

# 25. CI Requirements

P1-004 完成后 CI backend 必须继续执行：

```text
uv sync --locked
ruff check
ruff format --check
alembic upgrade head
alembic check
pytest
```

另外必须验证 P1-004 migration downgrade / re-upgrade。

可以：

- 在 CI 增加显式 `alembic downgrade b15a8d09c2f1` + re-upgrade；
- 或由 PostgreSQL integration test 明确完成等价验证。

但 Completion Report 必须能指出：

```text
P1-004 downgrade 到 b15a8d09c2f1 已真实执行
P1-003 tables remained intact
re-upgrade succeeded
```

CI 还需要实际运行：

```bash
uv run python -m scripts.seed_demo_workflows
```

环境：

```text
APP_ENV=test
```

Workflow Seed 不需要真实密码或公司数据。

P1-003 的 Demo User Seed CI 不得被删除。

---

# 26. Expected Package Structure

建议：

```text
backend/app/
├── domain/
│   └── workflow/
│       ├── __init__.py
│       ├── definition.py
│       └── errors.py
├── models/
│   └── workflow.py
└── workflow/
    ├── __init__.py
    └── repository.py

backend/scripts/
└── seed_demo_workflows.py

backend/tests/
├── workflow/
│   ├── test_workflow_definition.py
│   └── test_seed_demo_workflows.py
└── test_workflow_persistence.py
```

允许根据现有仓库风格做等价调整。

禁止为了目录形式制造大量无意义空层。

---

# 27. Comments / Docstrings

继续执行项目规范：

- 所有函数 / 方法写简洁中文 docstring。
- Domain dataclass / Enum / exception 写中文 docstring。
- ORM Model 写中文 docstring。
- Test function 写一行中文 docstring。
- 关键注释解释“为什么”。

尤其解释：

1. 为什么 Workflow Definition 要版本化。
2. 为什么历史版本不可原地修改。
3. 为什么只允许一个 active version。
4. 为什么 active=false 不代表历史版本失效。
5. 为什么 Node role 不等于具体审批人。
6. 为什么 P1-004 不修改 ApprovalApplication。
7. 为什么 Seed 不覆盖已有同版本不同 payload。

不要写机械逐行注释。

---

# 28. Security / Trust Boundaries

Workflow Definition 属于受信任的服务端配置。

禁止：

- LLM 自由生成 role 后直接持久化。
- Request / Tool 参数绕过 Domain validation 写 definition_json。
- ADMIN 自动成为所有 Workflow Node 的合法审批角色。
- Seed 使用真实员工数据。
- 在 Workflow JSON 中硬编码真实员工姓名 / 工号 / 邮箱。

后续 Agent 若查询 Workflow Definition，也只能读取受信任定义，不得自行改写流程版本。

---

# 29. Design Decisions Frozen in This TASK

以下决策在 P1-004 冻结：

1. 一期 Workflow Definition 不是 BPMN。
2. 一个 Definition Version 使用 JSONB 快照。
3. JSON 包含 code/version/nodes。
4. Node 一期只有 key + role。
5. Node 顺序由 nodes 数组顺序确定。
6. role 复用 P1-003 UserRole。
7. Node role 只允许 APPROVER / DATA_ADMIN。
8. code / node key 使用 lowercase snake_case。
9. `(code, version)` 唯一。
10. version 必须 > 0。
11. 同 code 最多一个 active version。
12. definition 版本不可原地修改。
13. active 可变化。
14. inactive 历史版本必须保留。
15. 不解析具体 assignee。
16. 不修改 ApprovalApplication。
17. Demo workflow = supplier_change v1。
18. Seed 与 migration 分离。
19. production 禁止 Demo workflow seed。
20. P1-004 不提供 HTTP API。

如 Codex 认为必须改变其中任一项：

停止实现并提交 Architecture Deviation。

不得自行修改。

---

# 30. Verification Commands

至少实际执行：

```powershell
cd backend

uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest

uv run alembic upgrade head
uv run alembic check
uv run alembic current

# P1-004 migration smoke
uv run alembic downgrade b15a8d09c2f1
uv run alembic upgrade head

# Demo workflow seed
$env:APP_ENV="test"
uv run python -m scripts.seed_demo_workflows

git diff --check
git status --short
```

如果本地 PostgreSQL 不可用：

- 不得声称 DB tests 通过。
- Push PR。
- 由 GitHub CI PostgreSQL 16 真实执行 DB tests / migration / seed。
- Completion Report 明确区分 local 与 CI。

---

# 31. Definition of Done

- [ ] WorkflowNodeDefinition frozen domain type
- [ ] WorkflowDefinitionSpec frozen domain type
- [ ] code / version / nodes validation 完整
- [ ] Node role 复用 UserRole
- [ ] APPROVER / DATA_ADMIN allowed
- [ ] APPLICANT / ADMIN rejected as node role
- [ ] JSON parse / serialize 稳定
- [ ] first / final / get / next / previous navigation 完整
- [ ] 单节点流程语义正确
- [ ] workflow_definitions table 存在
- [ ] JSONB NOT NULL
- [ ] version > 0 CHECK
- [ ] UNIQUE(code, version)
- [ ] active partial UNIQUE
- [ ] ORM 与 migration 无 drift
- [ ] Repository 无隐式 commit
- [ ] Definition immutability 得到保护
- [ ] Demo seed 幂等
- [ ] Seed 不覆盖 immutable version
- [ ] Seed 不会把 newer active version 降级回 v1
- [ ] production 禁止 Demo seed
- [ ] Migration parent = b15a8d09c2f1
- [ ] downgrade 保留 P1-001 / P1-003 tables
- [ ] 不修改 ApprovalApplication / ApprovalRecord
- [ ] 不修改 P1-002 State Machine
- [ ] 不实现 Workflow Instance
- [ ] 不实现 Approval Service
- [ ] P1-001 / P1-002 / P1-003 regressions 全绿
- [ ] Ruff / pytest / git diff --check 通过
- [ ] backend / frontend CI 全绿
- [ ] 未开始 P1-005

---

# 32. Completion Report

Codex 实现完成后必须输出：

```text
TASK Completion Report

TASK ID:
TASK-P1-004

Status:
READY_FOR_REVIEW

Changed files:

Workflow domain types:

Workflow validation:

Workflow navigation:

JSON codec:

Database table:

Database constraints / indexes:

Version immutability:

Active version behavior:

Repository behavior:

Demo workflow seed:

Seed immutability behavior:

Seed active-version behavior:

Alembic migration:

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

Status 不得写 DONE。

只能：

```text
READY_FOR_REVIEW
```

等待 Architecture / Code Review。

---

# 33. Codex Pre-Implementation Plan Prompt

在实现前，Codex 必须先只输出 Pre-Implementation Plan。

建议使用：

```text
当前进入 TASK-P1-004 — Workflow Definition。

Source of Truth：

- CODEX.md
- docs/V2.1-technical-design.md
- docs/V3.0-codex-execution-manual.md
- docs/tasks/TASK-P1-004.md

其中 P1-004 具体范围以：

docs/tasks/TASK-P1-004.md

为最高优先级。

不要立即编码。

先检查：

git rev-parse --show-toplevel
git branch --show-current
git rev-parse HEAD
git status --short

预期：

branch:
task/p1-004

基线必须包含已合并 P1-003 main：
2d510596f5496d9284f4b5cf9fc040503ad5ee3f

工作区必须干净。

如果不满足：

停止。
不要 reset --hard。
不要自行 rebase / merge。
报告实际状态。

然后完整阅读：

- CODEX.md
- docs/V2.1-technical-design.md
- docs/V3.0-codex-execution-manual.md
- docs/tasks/TASK-P1-004.md
- backend/app/domain/auth/**
- backend/app/domain/approval/**
- backend/app/models/**
- backend/app/auth/**
- backend/app/infrastructure/database.py
- backend/alembic/**
- backend/scripts/**
- backend/tests/**
- .github/workflows/ci.yml

本轮只输出：

Pre-Implementation Plan — TASK-P1-004

必须至少说明：

1. TASK 目标与边界
2. 预计新增/修改文件
3. WorkflowNodeDefinition 设计
4. WorkflowDefinitionSpec 设计
5. WorkflowDefinitionError / error code 设计
6. code / version / node key / node role validation
7. JSON strict parsing / stable serialization
8. first/final/get/next/previous navigation
9. 单节点流程语义
10. workflow_definitions ORM
11. JSONB / UUID / timestamp 设计
12. CHECK(version > 0)
13. UNIQUE(code, version)
14. active partial unique index
15. definition immutability enforcement
16. active 可变、payload 不可变边界
17. WorkflowDefinitionRepository
18. Repository transaction boundary
19. row metadata 与 JSON code/version 一致性
20. Demo supplier_change v1 seed
21. Seed idempotency
22. Seed immutable conflict
23. Seed 遇到 newer active version 的行为
24. production seed protection
25. Alembic parent / downgrade / upgrade
26. PostgreSQL integration test matrix
27. Domain architecture dependency tests
28. CI workflow adjustments
29. 如何保证不修改 ApprovalApplication / State Machine
30. Architecture risks / reviewer decisions
31. 明确 Out of Scope

不要编码。
不要生成 migration。
不要安装依赖。
不要修改文件。

等待 Architecture Review APPROVE 后再实现。
```
