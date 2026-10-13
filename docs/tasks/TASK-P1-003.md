# TASK-P1-003 — Demo Users / Authentication / Roles

## 1. Task Metadata

- **TASK ID**: `TASK-P1-003`
- **Phase**: Phase 1 — Approval Backend
- **Priority**: P0
- **Status**: READY
- **Depends on**:
  - `TASK-P1-001` Approval Domain Models / Migration
  - `TASK-P1-002` Approval State Machine
- **Source of Truth**:
  - `CODEX.md`
  - `docs/V2.1-technical-design.md`
  - `docs/V3.0-codex-execution-manual.md`
  - 本文件

> 若 V3.0 中旧的 P1 TASK 编号与本文件冲突，以 `docs/tasks/TASK-P1-003.md` 为本 TASK 的执行基线。

## 2. Goal

建立一套可独立运行、可撤销、可测试的 Demo Authentication 基础设施，为后续 Approval Service、ToolExecutor 和 Agent 提供可信的服务端身份与角色上下文。

本 TASK 必须回答：

> 当前请求是谁发起的？服务端如何证明这个身份？这个身份拥有哪些可信角色？后续业务代码如何拿到不可由前端、LLM 或 Tool 参数伪造的 `AuthContext`？

目标链路：

```text
username + password
        ↓
服务端认证
        ↓
Auth Session
        ↓
Opaque Bearer Token
        ↓
服务端解析 Session / User / Roles
        ↓
AuthContext
        ↓
后续 Approval Service / ToolExecutor
```

## 3. Architecture Decisions

### 3.1 Login

一期只实现：

```text
username + password
```

不实现注册、SSO、OAuth、MFA、密码找回或 Refresh Token。

### 3.2 Token Strategy

使用：

```text
Opaque Bearer Token
```

不使用 JWT。

原因：

- Demo 规模下每次请求查服务端 Session 成本可接受。
- 角色修改可在下一次请求立即生效。
- Session 可直接撤销。
- 不需要额外处理 JWT role stale、refresh token、blacklist 等复杂度。
- 后续如演进为分布式架构，再评估 JWT / 外部身份服务。

### 3.3 Password Hashing

使用：

```text
Argon2id
```

建议依赖：

```text
pwdlib[argon2]
```

禁止：

- 明文密码
- MD5
- 单纯 SHA-256(password)
- 自行实现密码哈希算法

### 3.4 Token Storage

原始 Token 只返回给客户端。

数据库只保存：

```text
SHA-256(raw_token)
```

Token 必须由高熵安全随机源生成，例如：

```python
secrets.token_urlsafe(32)
```

数据库不得保存 raw bearer token。

### 3.5 Trusted Identity

后续业务中的：

```text
acting_user_id
roles
```

只能来自服务端认证上下文。

禁止相信：

- Request Body 中自由传入的 user_id / role
- 前端声称的角色
- LLM 生成的 user_id / role
- Tool 参数中的身份声明

## 4. UserRole

在 Domain 层定义唯一角色枚举：

```text
APPLICANT
APPROVER
DATA_ADMIN
ADMIN
```

建议：

```python
class UserRole(StrEnum):
    """定义 Demo 系统中受信任的用户角色。"""

    APPLICANT = "APPLICANT"
    APPROVER = "APPROVER"
    DATA_ADMIN = "DATA_ADMIN"
    ADMIN = "ADMIN"
```

### 4.1 ADMIN Rule

`ADMIN` 不是超级业务角色。

只有 `ADMIN` 的用户不得自动获得：

- `APPLICANT`
- `APPROVER`
- `DATA_ADMIN`

例如：

```text
roles = {ADMIN}
required = {APPROVER}

=> AUTH_PERMISSION_DENIED
```

如 Admin 需要审批权限，应显式同时分配 `APPROVER`。

## 5. Database Schema

本 TASK 新增三张表：

```text
users
user_roles
auth_sessions
```

关系：

```text
User
 ├── 1:N UserRoleAssignment
 └── 1:N AuthSession
```

### 5.1 users

表名：

```text
users
```

字段：

```text
id
username
display_name
password_hash
enabled
created_at
updated_at
```

要求：

- `id`: UUID PK，应用侧生成。
- `username`: `VARCHAR(64)`，UNIQUE，NOT NULL。
- `display_name`: `VARCHAR(128)`，NOT NULL。
- `password_hash`: `VARCHAR(255)`，NOT NULL。
- `enabled`: BOOLEAN，NOT NULL，默认 true。
- `created_at` / `updated_at`: timezone-aware datetime。
- username 入库前统一 `strip + lower`。
- 一期 Demo username 使用 ASCII，不引入国际化用户名规范。
- 不增加 email / phone / department / employee_id / avatar 等无当前需求字段。

### 5.2 user_roles

表名：

```text
user_roles
```

字段：

```text
user_id
role
```

要求：

- `user_id`: FK → `users.id`
- `role`: `UserRole` 对应的非 native Enum / CHECK 约束
- 组合主键：

```text
PRIMARY KEY(user_id, role)
```

避免同一用户重复分配同一角色。

推荐 ORM 类名：

```text
UserRoleAssignment
```

避免与 `UserRole` Enum 重名。

### 5.3 auth_sessions

表名：

```text
auth_sessions
```

字段：

```text
id
user_id
token_hash
expires_at
revoked_at
created_at
```

要求：

- `id`: UUID PK。
- `user_id`: FK → `users.id`。
- `token_hash`: `VARCHAR(64)`，UNIQUE，NOT NULL。
- `expires_at`: timezone-aware datetime，NOT NULL。
- `revoked_at`: timezone-aware datetime，nullable。
- `created_at`: timezone-aware datetime，NOT NULL。
- 对 `user_id` 和 `expires_at` 建索引。
- `token_hash` 的 UNIQUE 本身提供唯一索引能力，不额外重复创建同义索引。
- 不保存 raw token。

### 5.4 Delete Policy

P1-003 不提供用户删除功能。

因此本 TASK 不依赖数据库级 `ON DELETE CASCADE` 完成业务清理；保持关系显式，避免无需求的级联删除规则。

## 6. Demo Users

Seed 四个虚构账号：

| username | display_name | role |
|---|---|---|
| `applicant` | Demo Applicant | `APPLICANT` |
| `manager` | Demo Manager | `APPROVER` |
| `data_admin` | Demo Data Admin | `DATA_ADMIN` |
| `admin` | Demo Admin | `ADMIN` |

注意：

- `manager` 只是 Demo username，不新增 `MANAGER` Role。
- 所有账号均为虚构 Demo 数据。
- 不得写入真实员工姓名、邮箱、工号或公司身份信息。

## 7. Settings

新增配置：

```text
AUTH_SESSION_TTL_MINUTES=480
DEMO_USER_PASSWORD=replace-with-local-demo-password
```

建议 Settings：

```python
auth_session_ttl_minutes: int = 480
demo_user_password: SecretStr | None = None
```

要求：

- TTL 必须为正数。
- `DEMO_USER_PASSWORD` 只用于本地 seed。
- 不记录 SecretStr 明文。
- `.env.example` 只能放占位值，不能放真实密码。

## 8. Password Service

提供独立 Password Hasher 抽象或等价边界：

```python
class PasswordHasher:
    """负责密码哈希和验证，不向业务层暴露底层算法细节。"""

    def hash(self, password: str) -> str:
        ...

    def verify(self, password: str, password_hash: str) -> bool:
        ...
```

要求：

- 底层使用 Argon2id。
- 相同密码多次 hash 结果应不同。
- 正确密码可以验证。
- 错误密码验证失败。
- 密码 hash 不写日志。

### 8.1 Async Boundary

Argon2 属于 CPU 密集操作。

FastAPI async 路径中不得长时间直接阻塞 event loop。

允许使用：

```python
await asyncio.to_thread(...)
```

或等价方式在边界执行 hash / verify。

## 9. Username Normalization

统一函数：

```text
strip
↓
lower
```

例如：

```text
" Manager " -> "manager"
```

Normalization 必须收口在 Auth Service / security helper，不能由 Router、前端或 Repository 各自实现不同版本。

## 10. Auth Session Lifecycle

默认 Session TTL：

```text
8 hours
```

即：

```text
480 minutes
```

登录成功：

```text
created_at = now
expires_at = now + configured TTL
revoked_at = null
```

Session 有效条件：

```text
token exists
AND revoked_at IS NULL
AND expires_at > now
AND user.enabled = true
```

P1-003 不实现后台清理 expired session 的 job；允许 expired rows 暂时保留。

## 11. AuthContext

定义不可变的可信内部身份对象：

```python
@dataclass(frozen=True)
class AuthContext:
    """表示服务端完成认证后得到的可信调用者身份。"""

    session_id: UUID
    user_id: UUID
    username: str
    roles: frozenset[UserRole]
```

允许增加 `display_name` 等非敏感展示字段，但不得包含：

- password
- password_hash
- raw token
- token_hash

### 11.1 Future Integration

后续 Approval Service 构建 P1-002 `TransitionContext` 时：

```python
TransitionContext(
    actor_id=str(auth_context.user_id),
    applicant_id=approval.applicant_id,
    current_assignee_ids=current_assignee_ids,
    ...
)
```

禁止：

```text
request.actor_id
LLM.actor_id
tool_args.actor_id
```

直接成为可信操作者身份。

## 12. Repository Boundary

Repository 只负责持久化。

至少需要支持等价能力：

```text
find_user_by_username
load_user_roles
create_auth_session
find_auth_session_by_token_hash
revoke_auth_session
```

Repository 禁止负责：

- FastAPI Request / Response
- HTTP status
- Password policy
- 业务权限决策
- LLM / Agent

## 13. Auth Service Boundary

Auth Service 负责：

- username normalize
- password verify
- raw token 生成
- token SHA-256
- session 创建
- session 验证
- AuthContext 构建
- logout / revoke current session

API Router 只负责：

- Pydantic request/response
- Authorization Header / Depends
- Error mapping
- HTTP response

## 14. REST API

本 TASK 只实现三个 Auth API。

### 14.1 Login

```http
POST /api/v1/auth/login
```

Request：

```json
{
  "username": "manager",
  "password": "local-demo-password"
}
```

成功响应建议：

```json
{
  "access_token": "opaque-random-token",
  "token_type": "bearer",
  "expires_in": 28800,
  "user": {
    "id": "uuid",
    "username": "manager",
    "display_name": "Demo Manager",
    "roles": ["APPROVER"]
  }
}
```

要求：

- 成功响应增加 `Cache-Control: no-store`。
- 不返回 password / password_hash / token_hash。

### 14.2 Current User

```http
GET /api/v1/auth/me
Authorization: Bearer <token>
```

返回：

```text
id
username
display_name
roles
```

不得返回任何密码或 Token hash。

### 14.3 Logout

```http
POST /api/v1/auth/logout
Authorization: Bearer <token>
```

成功：

```http
204 No Content
```

行为：

```text
AuthContext.session_id
        ↓
auth_sessions
        ↓
revoked_at = now()
```

撤销后该 Token 再访问受保护 API 应返回 401。

## 15. Bearer Authentication

建议使用 FastAPI 标准 Bearer 解析能力或等价实现，但必须保证错误响应使用项目统一 Error Envelope。

处理流程：

```text
Authorization: Bearer raw_token
        ↓
SHA-256(raw_token)
        ↓
auth_sessions.token_hash
        ↓
检查 revoked / expired
        ↓
加载 User
        ↓
检查 enabled
        ↓
加载 Roles
        ↓
AuthContext
```

## 16. Role Dependency

提供等价依赖：

```python
require_any_role(UserRole.APPROVER, UserRole.DATA_ADMIN)
```

语义：

```text
current roles 与 required roles 有交集
=> allow

否则
=> AUTH_PERMISSION_DENIED
```

特别测试：

```text
current roles = {ADMIN}
required = {APPROVER}

=> 403
```

## 17. Error Model

Auth 错误必须使用 V2.1 统一 Envelope：

```json
{
  "error": {
    "code": "AUTH_INVALID_CREDENTIALS",
    "message": "用户名或密码错误",
    "request_id": "req_xxx",
    "retryable": false,
    "details": {}
  }
}
```

稳定错误码：

| Code | HTTP | 场景 |
|---|---:|---|
| `AUTH_INVALID_CREDENTIALS` | 401 | username/password 错误、disabled user 登录 |
| `AUTH_UNAUTHENTICATED` | 401 | Token 缺失、格式错误、未知、已 revoked |
| `AUTH_TOKEN_EXPIRED` | 401 | Session 已过期 |
| `AUTH_USER_DISABLED` | 403 | 用户登录后账号被禁用 |
| `AUTH_PERMISSION_DENIED` | 403 | 已认证但不具备要求角色 |

要求：

- 401 响应包含 `WWW-Authenticate: Bearer`。
- Error Envelope 中必须带当前 `request_id`。
- `retryable=false`。
- `details` 默认空对象。
- 未知用户、错误密码、disabled user 的登录响应统一为 `AUTH_INVALID_CREDENTIALS`，避免直接暴露账号是否存在。

## 18. Seed Strategy

Seed 数据不得写入 Alembic migration。

新增独立脚本，例如：

```text
backend/scripts/seed_demo_users.py
```

要求：

- 从 `DEMO_USER_PASSWORD` 读取 Demo 密码。
- 密码必须经过 Argon2id hash 后入库。
- `APP_ENV == production` 时拒绝执行。
- 缺少 Demo password 时明确失败。
- 重复执行不得产生重复用户或重复 role assignment。
- 可采用 upsert / reconcile 语义，使四个 Demo 用户最终与定义一致。
- 不打印密码、hash、raw token。
- 不写真实公司数据。

## 19. Approval Domain Decoupling

P1-003 不修改已有审批表来绑定 User FK。

保持：

```text
ApprovalApplication.applicant_id: str
ApprovalRecord.operator_id: str
```

后续写入时使用：

```text
str(auth_context.user_id)
```

本 TASK 禁止：

- 给 `approval_application.applicant_id` 增加 `users.id` FK
- 给 `approval_record.operator_id` 增加 `users.id` FK
- 修改 P1-002 State Machine 业务规则

## 20. Suggested Package Structure

允许在符合现有仓库结构的前提下做等价调整。

建议：

```text
backend/app/
├── api/
│   ├── deps.py
│   └── v1/
│       └── auth.py
├── auth/
│   ├── __init__.py
│   ├── context.py
│   ├── errors.py
│   ├── repository.py
│   ├── schemas.py
│   ├── security.py
│   └── service.py
├── domain/
│   └── auth/
│       ├── __init__.py
│       └── enums.py
└── models/
    └── auth.py

backend/tests/
├── auth/
│   ├── test_password_hasher.py
│   ├── test_auth_service.py
│   └── test_auth_api.py
└── test_auth_persistence.py

backend/scripts/
└── seed_demo_users.py
```

不要为了目录美观生成大量空文件。

## 21. Alembic

新增 P1-003 migration，父 revision 为当前：

```text
7aeecb191822
```

Migration 只新增：

```text
users
user_roles
auth_sessions
```

要求：

- 空库 `alembic upgrade head` 成功。
- 从当前 P1-002 数据库升级成功。
- `downgrade` 本次 migration 后，P1-001 三张审批表仍保留。
- 再次 `upgrade head` 成功。
- ORM metadata 与 migration 无 drift。
- migration 不写 Demo seed 数据。

## 22. Required Tests

### 22.1 Password

1. hash 结果不等于明文。
2. 正确密码 verify → true。
3. 错误密码 verify → false。
4. 相同密码两次 hash 结果不同。

### 22.2 User / Role Persistence

5. `username` UNIQUE。
6. 非法 Role 被数据库约束拒绝。
7. 重复 `(user_id, role)` 被主键/唯一约束拒绝。
8. User 与 role assignments relationship 正确。
9. timezone-aware 时间字段正确。

### 22.3 Session Persistence

10. `token_hash` UNIQUE。
11. raw token 不进入数据库。
12. session → user relationship 正确。
13. `revoked_at` nullable。
14. `expires_at` timezone-aware。

### 22.4 Login

15. 正确 username/password → 200。
16. 不存在 username → 401 `AUTH_INVALID_CREDENTIALS`。
17. 错误密码 → 同样 401。
18. disabled user 登录 → 同样 401。
19. username 大小写/前后空格按统一 normalization 登录成功。
20. Login response 无 password/password_hash/token_hash。
21. Login response 带 `Cache-Control: no-store`。

### 22.5 Auth Session Resolution

22. 正确 Token → 构建正确 `AuthContext`。
23. 随机未知 Token → 401 `AUTH_UNAUTHENTICATED`。
24. malformed / missing Bearer → 401 `AUTH_UNAUTHENTICATED`。
25. revoked Token → 401 `AUTH_UNAUTHENTICATED`。
26. expired Token → 401 `AUTH_TOKEN_EXPIRED`。
27. 用户登录后被 disabled → 403 `AUTH_USER_DISABLED`。
28. 401 响应包含 `WWW-Authenticate: Bearer`。
29. Error Envelope 包含 request_id。

### 22.6 /me / Logout

30. `GET /api/v1/auth/me` 正确返回 user + roles。
31. `/me` 不返回敏感字段。
32. `POST /api/v1/auth/logout` → 204。
33. logout 后原 Token 再访问 `/me` → 401。

### 22.7 Role Guard

34. `APPROVER` 满足 required `APPROVER`。
35. `APPLICANT` 请求 required `APPROVER` → 403。
36. 只有 `ADMIN` 请求 required `APPROVER` → 403。
37. 多角色用户只要有任一 required role 即允许。

### 22.8 Seed

38. 首次执行创建四个 Demo 用户。
39. 第二次执行不产生重复用户。
40. 第二次执行不产生重复 role assignment。
41. production 环境拒绝 seed。
42. Seed 不输出密码 / hash。

### 22.9 Migration / Regression

43. `alembic upgrade head`。
44. downgrade 本次 migration 到 `7aeecb191822`。
45. 再次 `alembic upgrade head`。
46. `alembic check` 无 schema drift。
47. 原 P1-001 / P1-002 tests 保持通过。
48. CI backend / frontend 保持通过。

## 23. Security Boundaries

必须保证：

- 密码不以明文存储。
- raw bearer token 不入库。
- Token / password / password_hash / token_hash 不进入 access log。
- Login / Auth API 不返回 hash。
- disabled user 登录不暴露“用户存在但被禁用”。
- Role 来自数据库，不来自 request body。
- `AuthContext` 由服务端构建。
- `ADMIN` 不隐式绕过业务角色检查。
- 本 TASK 不让 Agent / Tool 直接接触 Repository。

## 24. Comments / Docstrings

延续仓库规范：

- 所有函数/方法写简洁中文 docstring。
- 所有 ORM Model / Enum / Dataclass / 异常类写中文 docstring。
- 测试函数写一行中文 docstring，说明验证行为。
- 关键注释解释“为什么”，尤其：
  - 为什么用 Opaque Session 而不是 JWT。
  - 为什么数据库只存 token_hash。
  - 为什么 ADMIN 不是业务超级权限。
  - 为什么身份只能由服务端 AuthContext 注入。
  - 为什么 Seed 与 migration 分离。
- 不写机械逐行注释。

## 25. Out of Scope

禁止实现：

- 用户注册
- 修改密码 / 忘记密码
- MFA
- SSO / OAuth
- JWT / Refresh Token
- 登录限频 / CAPTCHA
- 复杂 Permission 表
- 组织架构 / 部门
- 用户管理后台
- Agent Session
- Tool permission execution
- WorkflowDefinition
- MasterDataEntity
- Approval Service
- submit / approve / reject / withdraw API
- Optimistic locking
- Idempotency
- HITL
- Agent / LLM / Tool
- 前端登录页

登录限频属于后续 Hardening，不在本 TASK 为此引入 Redis 认证逻辑。

## 26. Verification Commands

至少实际执行：

```powershell
cd backend

uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run alembic check
uv run alembic current

# 数据库 migration smoke
uv run alembic upgrade head
uv run alembic downgrade 7aeecb191822
uv run alembic upgrade head

git diff --check
git status --short
```

如新增依赖导致 lockfile 更新，必须提交 `uv.lock`。

## 27. Definition of Done

- [ ] users / user_roles / auth_sessions 三表存在
- [ ] UserRole 只有 APPLICANT / APPROVER / DATA_ADMIN / ADMIN
- [ ] ADMIN 不具备隐式超级业务权限
- [ ] 密码使用 Argon2id
- [ ] 数据库不保存 raw Token
- [ ] Opaque Bearer Token 可登录并恢复 AuthContext
- [ ] /me 返回可信身份与角色
- [ ] /logout 可撤销当前 Session
- [ ] Role 来自数据库
- [ ] disabled / expired / revoked 行为正确
- [ ] 统一 AUTH_* error code
- [ ] 401 带 WWW-Authenticate: Bearer
- [ ] Error Envelope 带 request_id
- [ ] Seed 与 migration 分离
- [ ] production 禁止 Demo Seed
- [ ] 不修改 ApprovalApplication / ApprovalRecord schema
- [ ] 不修改 P1-002 State Machine
- [ ] Migration upgrade/downgrade/upgrade 成功
- [ ] Alembic 无 schema drift
- [ ] 原 Approval tests 全绿
- [ ] Ruff / pytest / git diff --check 通过
- [ ] CI 全绿
- [ ] 未开始 P1-004

## 28. Completion Report

Codex 完成后必须输出：

```text
TASK Completion Report

TASK ID:
Status: READY_FOR_REVIEW

Changed files:
Dependencies added:
Auth domain types:
Database tables:
Database constraints / indexes:
Password hashing:
Token generation / hashing:
AuthContext:
Auth service:
Auth repository:
Auth API:
Role guard:
Seed script:
Alembic migration:
Upgrade verification:
Downgrade verification:
API integration tests:
Security verification:
Commands actually run:
Tests passed:
Tests failed:
Code comments / docstrings:
Deviations:
Known limitations:
git diff --stat:
git status --short:
Commit SHA:
Push result:
Suggested next task:
```

不得自行写 `DONE`。

## 29. Codex Start Prompt

```text
当前进入 TASK-P1-003。

本 TASK 是 Demo Users / Authentication / Roles。

注意：
V3.0 历史版本中的 P1 TASK 编号曾与当前实际开发顺序不一致。
本 TASK 的执行 Source of Truth 是：

- CODEX.md
- docs/V2.1-technical-design.md
- docs/V3.0-codex-execution-manual.md
- docs/tasks/TASK-P1-003.md

其中对 P1-003 的具体范围，以 docs/tasks/TASK-P1-003.md 为准。

不要立即编码。

先执行：

git rev-parse --show-toplevel
git branch --show-current
git status --short
git rev-parse HEAD

预期：
- 当前分支：task/p1-003
- 工作区干净
- 分支基于已合并 P1-002 的 main

如果不满足，停止并报告。

然后完整阅读上述四份文档和现有：
- backend/app/core/config.py
- backend/app/main.py
- backend/app/infrastructure/database.py
- backend/app/models/**
- backend/alembic/**
- backend/tests/**

只输出 Pre-Implementation Plan：

1. TASK-P1-003 目标
2. 预计新增/修改文件
3. UserRole 设计
4. User / UserRoleAssignment / AuthSession ORM 设计
5. FK / UNIQUE / PK / INDEX 设计
6. Argon2id PasswordHasher 设计
7. username normalization 设计
8. Opaque Token 生成与 SHA-256 存储设计
9. Session TTL / expired / revoked 设计
10. AuthContext 设计
11. Repository 边界
12. AuthService 边界
13. GET current auth dependency 设计
14. require_any_role 设计
15. login / me / logout API 设计
16. AUTH_* Error Mapping 与 request_id 设计
17. Demo Seed 设计与 production 保护
18. Alembic migration 方案
19. 单元 / Repository / API / migration 测试矩阵
20. CPU 密集密码 hash/verify 如何避免阻塞 event loop
21. 如何保证不修改 Approval Domain / State Machine
22. 架构风险与需要 Reviewer 确认的点
23. 明确不会实现的范围

不要编码。
不要生成 migration。
不要修改文件。
等待 Architecture Review APPROVE 后再实现。
```
