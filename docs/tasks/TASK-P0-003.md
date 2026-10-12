# TASK-P0-003 — PostgreSQL / Redis 应用连接与 Alembic 基础设施

## 1. Task Metadata
- TASK ID: `TASK-P0-003`
- Phase: Phase 0 — Project Foundation
- Priority: P0
- Status: READY
- Depends on: `TASK-P0-002`

## 2. Goal
在不实现任何业务表、审批逻辑或 Agent 功能的前提下，建立：
1. PostgreSQL 异步连接
2. SQLAlchemy 2.x Async Engine / AsyncSession
3. Redis asyncio client
4. FastAPI lifespan 统一初始化与释放
5. Alembic 基础迁移框架
6. `/ready` readiness 检查
7. 对应单元测试和本地 Docker 集成验证

## 3. Core Architecture

```text
Uvicorn
  ↓
RequestIdMiddleware (final ASGI app)
  ↓
FastAPI api
  ↓
lifespan
  ├── PostgreSQL Async Engine / Session Factory
  └── Redis Async Client
```

健康检查分离：

```text
GET /health
  → liveness
  → 只表示进程活着
  → {"status":"ok"}

GET /ready
  → readiness
  → PostgreSQL SELECT 1
  → Redis PING
```

原则：数据库或 Redis 暂时不可用时，`/health` 仍应 200，但 `/ready` 应返回 503。

## 4. Scope

### 4.1 Settings
在现有 `Settings` 增加：

```text
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
POSTGRES_HOST
POSTGRES_PORT

REDIS_HOST
REDIS_PORT
REDIS_DB
```

要求：
- Python 属性使用 snake_case
- 环境变量继续使用大写名称
- 不记录数据库密码
- 不把含密码 DSN 输出到日志
- `.env` 不提交 Git
- 数据库 URL 必须安全构造，不能用简单字符串拼接处理特殊字符

### 4.2 PostgreSQL
使用：
- SQLAlchemy 2.x async API
- asyncpg

建议目录：

```text
backend/app/infrastructure/
├── __init__.py
├── database.py
└── redis.py
```

`database.py` 至少提供：
- AsyncEngine
- async_sessionmaker
- Declarative Base
- AsyncSession 获取入口
- 数据库资源释放

要求：
- Engine 进程级复用
- Session 不得全局共享
- 禁止每请求创建 Engine
- 后续业务代码不自行 `create_async_engine()`

### 4.3 Redis
使用 `redis.asyncio.Redis`。

要求：
- client 进程级复用
- 不得每请求新建 client
- 提供统一初始化 / 获取 / 关闭入口
- 本 TASK 不实现业务缓存、锁、幂等、Memory

### 4.4 FastAPI lifespan
使用 FastAPI lifespan 统一管理：

```text
Startup:
  初始化 PostgreSQL
  初始化 Redis

Shutdown:
  关闭 Redis
  dispose DB Engine
```

要求：
- 生命周期集中管理
- 即使一个 shutdown 操作失败，也应继续尝试释放另一个资源
- Router / lifespan 配置继续挂在内部 `api`，不是外层 `app`

### 4.5 Alembic
建立：

```text
backend/alembic.ini
backend/alembic/
├── env.py
├── script.py.mako
└── versions/
```

要求：
- 不在 `alembic.ini` 硬编码真实数据库密码
- `target_metadata` 指向统一 `Base.metadata`
- 支持 async PostgreSQL
- 本 TASK 不创建 Approval/User/Task 等业务 migration

## 5. Readiness Endpoint

新增：

```http
GET /ready
```

成功示例：

```json
{
  "status": "ready",
  "postgres": "ok",
  "redis": "ok"
}
```

依赖失败：
- HTTP 503
- 稳定 JSON schema
- 不返回 traceback、DSN、密码、host 等敏感信息

数据库检查使用 `SELECT 1`，Redis 使用 `PING`。

`/health` 必须继续保持：

```json
{"status":"ok"}
```

## 6. Expected Files

允许新增 / 修改：

```text
.env.example

backend/pyproject.toml
backend/uv.lock

backend/app/main.py
backend/app/core/config.py

backend/app/infrastructure/__init__.py
backend/app/infrastructure/database.py
backend/app/infrastructure/redis.py

backend/alembic.ini
backend/alembic/env.py
backend/alembic/script.py.mako
backend/alembic/versions/

backend/tests/test_config.py
backend/tests/test_database.py
backend/tests/test_redis.py
backend/tests/test_readiness.py
backend/tests/test_lifespan.py

README.md
```

## 7. Out of Scope
禁止实现：
- User / Role / Permission 业务表
- Approval 业务表与状态机
- Agent / Tool / Router / Handoff
- RAG / Memory / MCP
- Distributed Lock / Idempotency
- Queue / Celery / Kafka
- Audit DB
- OpenTelemetry
- 前端数据库状态页面

## 8. Code Comment Requirements
关键架构逻辑必须写简洁中文注释，重点解释“为什么”：
- Async Engine 为什么进程级复用
- AsyncSession 为什么不能全局共享
- lifespan 为什么统一管理资源
- `/health` 与 `/ready` 为什么分离
- DB / Redis 释放逻辑
- Alembic metadata / Settings 接入
- 异常降级 / 兼容性处理

不要对显而易见的赋值、循环和简单判断逐行注释。

## 9. Required Tests
至少覆盖：

1. PostgreSQL Settings 默认值和环境变量覆盖
2. Redis Settings 默认值和覆盖
3. DB URL 对特殊字符密码处理正确
4. AsyncEngine / AsyncSession 工厂类型正确
5. 不共享全局 Session
6. Redis client 初始化参数正确
7. lifespan startup / shutdown
8. 一个资源 shutdown 失败时另一个仍尝试释放
9. `/health` 在 DB/Redis 失败时仍返回 200
10. `/ready` 成功返回 200
11. `/ready` 单依赖失败返回 503
12. readiness 响应不泄露 DSN / password / traceback
13. `/ready` 200 / 503 都带 `X-Request-ID`

## 10. Integration Verification

```powershell
docker compose up -d
docker compose ps
```

验证：
- PostgreSQL healthy，`SELECT 1` 成功
- Redis healthy，`PING` 返回 `PONG`

启动 API：

```powershell
cd backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8765
```

实际请求：

```text
GET /health → 200
GET /ready  → 200
```

均带 `X-Request-ID`。

## 11. Verification Commands

```powershell
cd backend

uv sync --locked
uv run python --version
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run alembic current

git diff --check
git status --short
```

Python 必须仍为 3.12.x。

## 12. Definition of Done

- [ ] SQLAlchemy 2.x async
- [ ] asyncpg
- [ ] redis asyncio client
- [ ] Engine 进程级复用
- [ ] Session 不全局共享
- [ ] Redis client 进程级复用
- [ ] lifespan 集中管理 startup / shutdown
- [ ] Alembic 可正常加载
- [ ] Alembic 无真实密码
- [ ] `/health` 不依赖 DB / Redis
- [ ] `/ready` 检查 PostgreSQL + Redis
- [ ] `/ready` 失败返回 503
- [ ] 不泄露敏感信息
- [ ] 200 / 503 均带 Request ID
- [ ] 未创建业务表
- [ ] 关键逻辑有简洁中文注释
- [ ] Ruff / pytest 通过
- [ ] Docker 实际连通验证通过
- [ ] 未开始下一个 TASK

## 13. Codex Start Prompt

```text
当前进入 TASK-P0-003。

不要立即编码。

请完整阅读：
CODEX.md
docs/V2.1-technical-design.md
docs/V3.0-codex-execution-manual.md
docs/tasks/TASK-P0-003.md

先执行：
git rev-parse --show-toplevel
git branch --show-current
git status --short

如果工作区不干净，停止并报告。

如果工作区干净，只输出 Pre-Implementation Plan：
1. TASK-P0-003 目标
2. 预计新增/修改文件
3. Settings 扩展方案
4. PostgreSQL Async Engine / AsyncSession 设计
5. Redis asyncio client 设计
6. FastAPI lifespan 设计
7. /health 与 /ready 的职责划分
8. Alembic 方案
9. 测试方案
10. 集成验证方案
11. 关键中文注释将写在哪些位置
12. 架构风险
13. 明确不会实现的范围

不要编码。
等待 Architecture Review APPROVE 后再开始实现。
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
