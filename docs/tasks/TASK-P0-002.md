# TASK-P0-002 — Settings、Structured Logging 与 Request ID 基础设施

## 1. Task Metadata

- **TASK ID**: `TASK-P0-002`
- **Phase**: Phase 0 — Project Foundation
- **Priority**: P0
- **Status**: READY
- **Depends on**: `TASK-P0-001`
- **Next task after approval**: `TASK-P0-003`

## 2. Goal

在不进入数据库、审批业务或 Agent 业务实现的前提下，为后端建立统一、可测试、可扩展的基础运行配置：

1. `Settings`（配置管理）
2. `.env.example`（公开的环境变量模板）
3. Structured Logging（结构化日志基础）
4. `request_id`（请求级关联 ID）
5. `/health` 保持兼容并纳入统一日志链路

本 TASK 的目的不是做完整 Observability，而是为后续 DB、Tool、Agent、Trace 和 Audit 提供统一基础。

## 3. Scope

### 3.1 Must Implement

#### A. Settings

使用 `pydantic-settings` 实现统一 Settings。

建议文件：

```text
backend/app/core/
├── __init__.py
├── config.py
├── logging.py
└── request_context.py
```

至少包含以下配置：

```text
APP_NAME
APP_ENV
LOG_LEVEL
REQUEST_ID_HEADER
```

推荐默认值：

```text
APP_NAME=enterprise-agent
APP_ENV=development
LOG_LEVEL=INFO
REQUEST_ID_HEADER=X-Request-ID
```

约束：

- `APP_ENV` 至少允许：`development`、`test`、`production`
- `LOG_LEVEL` 至少允许：`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`
- 项目真实 `.env` 不允许提交 Git
- `.env.example` 不允许包含真实 token、password、company domain、company API
- Settings 应支持环境变量覆盖
- Settings 应提供单一入口，例如：`settings = get_settings()`
- 不允许在业务代码中到处直接调用 `os.getenv()`

#### B. .env.example

更新仓库根目录 `.env.example`，至少包含：

```dotenv
APP_NAME=enterprise-agent
APP_ENV=development
LOG_LEVEL=INFO
REQUEST_ID_HEADER=X-Request-ID

POSTGRES_DB=enterprise_agent
POSTGRES_USER=enterprise_agent
POSTGRES_PASSWORD=change_me
POSTGRES_HOST=localhost
POSTGRES_PORT=5432

REDIS_HOST=localhost
REDIS_PORT=6379
```

说明：

- PostgreSQL / Redis 字段可保留给后续 TASK 使用
- 本 TASK 不实现数据库或 Redis 应用连接
- `change_me` 仅为本地公开占位符

#### C. Request ID

为每个 HTTP 请求建立 request ID。

行为要求：

1. 如果请求头包含合法 `X-Request-ID`，复用该 ID
2. 如果没有，服务端生成 UUID
3. request ID 写入：
   - `request.state.request_id`
   - 响应头 `X-Request-ID`
   - 当前请求生命周期内的日志上下文
4. 不得依赖全局可变变量保存 request ID
5. 应使用异步安全机制，例如 `contextvars.ContextVar`
6. request 完成后必须清理 context，防止不同请求之间污染

对于异常、空字符串或明显不合理的 request ID，可生成新的 UUID，不应因此导致请求失败。

#### D. Structured Logging

建立统一日志初始化入口，例如：

```python
configure_logging(settings)
```

最低要求：

- 使用 Python 标准 `logging`
- 日志至少包含：
  - timestamp
  - level
  - logger
  - message
  - request_id
- request_id 不存在时使用明确默认值，例如 `"-"`
- 不记录：
  - Authorization
  - Cookie
  - API Key
  - Password
  - 完整请求 Body
- 不允许为了本 TASK 引入完整 Trace / OpenTelemetry 实现
- 日志初始化只能有一个清晰入口，避免不同模块各自 `basicConfig`

推荐实现方式：

```text
ContextVar
  ↓
Logging Filter / Formatter
  ↓
request_id 自动进入每条请求日志
```

#### E. HTTP Request Logging

为请求入口添加最基础的访问日志，至少能关联：

```text
request_id
method
path
status_code
duration_ms
```

限制：

- 不记录 query 中可能出现的敏感内容
- 不记录 request body
- 不记录完整 header
- `/health` 也应拥有 request_id

## 4. Out of Scope

本 TASK 明确禁止实现：

- PostgreSQL 应用连接
- Redis 应用连接
- SQLAlchemy models
- Alembic migration
- Approval Service
- Approval state machine
- User / Role / Permission 业务模型
- Approval Agent
- Contract Agent
- Tool Registry
- Tool Executor
- Router
- Agent Registry
- Handoff
- MCP
- RAG
- Memory
- OpenTelemetry Trace
- Audit database
- Metrics backend
- 前端业务功能

除非修复本 TASK 测试所必需，不修改前端。

## 5. Expected Files

Codex 可在以下范围内新增或修改文件：

```text
.env.example

backend/pyproject.toml
backend/uv.lock

backend/app/main.py

backend/app/core/__init__.py
backend/app/core/config.py
backend/app/core/logging.py
backend/app/core/request_context.py

backend/tests/test_config.py
backend/tests/test_request_id.py
backend/tests/test_logging.py
backend/tests/test_health.py

README.md
```

允许根据合理实现新增少量基础文件，但必须在 Completion Report 中说明原因。

## 6. Architecture Requirements

### 6.1 Settings Boundary

后续模块应依赖 Settings 对象，而不是散落调用 `os.getenv("XXX")`。

### 6.2 Request Context Boundary

request ID 属于 request context，而不是业务实体。

未来：

```text
HTTP Request ID
≠
Task ID
≠
Trace ID
≠
Tool Call ID
```

本 TASK 不得把这些概念混为一个字段。

### 6.3 Logging Boundary

logging 只解决基础运行日志。

未来的：

```text
Agent Trace
Tool Trace
Audit Log
Evaluation Run
```

是独立概念，不在本 TASK 中实现。

## 7. Tests Required

### TEST-001 Default Settings

验证默认 Settings：

```text
APP_ENV = development
LOG_LEVEL = INFO
REQUEST_ID_HEADER = X-Request-ID
```

### TEST-002 Environment Override

通过测试环境变量覆盖至少一个 Settings 字段，并验证生效。

测试结束后不得污染其他测试环境。

### TEST-003 Server Generates Request ID

请求 `GET /health`，不提供 `X-Request-ID`。

验证：

- HTTP 200
- Response Header 中存在合法 request ID

### TEST-004 Client Request ID Is Propagated

请求：

```http
GET /health
X-Request-ID: test-request-123
```

验证响应：

```http
X-Request-ID: test-request-123
```

### TEST-005 Request IDs Are Isolated

连续或并发发起不同请求，确保 request ID 不串请求。

### TEST-006 Logging Contains Request ID

在请求生命周期内产生一条日志，验证日志记录能够关联当前 request ID。

### TEST-007 No Secret Leakage

如果日志测试中传入 Authorization / Cookie 等测试数据，确保日志文本中没有这些敏感值。

## 8. Verification Commands

Codex 必须实际执行：

```powershell
cd backend

uv sync --locked

uv run python --version

uv run ruff check .

uv run ruff format --check .

uv run pytest
```

必须保持 Python 3.12.x。

然后启动 FastAPI 并验证两次 `/health`：

- Request A：不传 request ID，确认响应头自动生成
- Request B：传入 `X-Request-ID: manual-test-001`，确认响应同值，并检查日志能关联对应 request ID

## 9. Definition of Done

- [ ] Python 仍为 3.12 基线
- [ ] 使用 `pydantic-settings`
- [ ] Settings 有单一入口
- [ ] `.env.example` 无真实秘密
- [ ] 业务代码没有新增散落的 `os.getenv`
- [ ] request ID 可自动生成
- [ ] 客户端 request ID 可传播
- [ ] request ID 写入 Response Header
- [ ] request ID 可进入日志上下文
- [ ] request context 在请求后正确清理
- [ ] 请求日志包含 method/path/status/duration/request_id
- [ ] 日志不记录 Authorization/Cookie/Password/API Key
- [ ] `/health` 继续返回 HTTP 200
- [ ] Ruff lint 通过
- [ ] Ruff format check 通过
- [ ] pytest 全部通过
- [ ] 未实现任何 P0-003 或业务/Agent 功能
- [ ] Completion Report 完整

## 10. Reviewer Checklist

### Architecture

- Settings 是否集中管理
- request ID 是否使用 async-safe context
- request ID 是否错误地被当成 Task ID / Trace ID
- logging 是否只有统一配置入口

### Security

- 是否记录 Authorization / Cookie / Password
- `.env.example` 是否出现真实凭据
- 是否错误记录整个 request body

### Engineering

- 是否有测试覆盖传播、生成、隔离
- 是否保持 Python 3.12
- 是否无必要引入大型 logging 依赖
- 是否越界提前做 Observability / DB / Agent

## 11. Completion Report Requirements

完成后必须输出：

```text
TASK Completion Report

TASK ID:
Status:

Changed files:

Behavior implemented:

Architecture decisions:

Settings implemented:

Request ID behavior:

Logging behavior:

Commands actually run:

Tests passed:

Tests failed:

Security considerations:

Deviations from V2.1/V3.0:

Known limitations:

Remaining environment issues:

Git status --short:

Suggested next task:
```

如果所有 DoD 满足：

```text
Status: READY_FOR_REVIEW
```

不得自行开始 `TASK-P0-003`。

## 12. Codex Start Prompt

```text
请完整阅读：

CODEX.md
docs/V2.1-technical-design.md
docs/V3.0-codex-execution-manual.md
docs/tasks/TASK-P0-002.md

当前只允许实现 TASK-P0-002。

开始编码前先执行：

git rev-parse --show-toplevel
git status --short

确认当前仓库根目录正确且工作区干净。

然后输出：

1. 实现计划
2. 预计修改文件
3. 关键设计选择
4. 风险点

不要实现 TASK-P0-003 或任何 Approval / Agent / Tool / Contract / Router / MCP / RAG / Memory 功能。

完成后运行 TASK 文档要求的全部测试和验收命令，并按 CODEX.md 输出完整 Task Completion Report。

没有实际运行的命令，不允许声称通过。
```
代码注释要求：

1. 对关键架构逻辑添加简洁中文注释。
2. 注释重点解释“为什么这样设计”，不要只重复代码做了什么。
3. 以下场景必须注释：
   - Middleware / ContextVar 生命周期
   - 缓存与单例
   - 幂等处理
   - 异常与降级策略
   - 权限与安全检查
   - 状态机转换
   - Tool 调用边界
   - HITL 高风险操作
   - 数据库事务
   - 重试 / 超时
   - 不直观的兼容性处理
4. 不要对显而易见的赋值、循环、简单 if 做逐行注释。
5. 注释默认使用中文，代码标识符保持英文。