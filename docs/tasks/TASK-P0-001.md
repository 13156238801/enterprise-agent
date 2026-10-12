# TASK-P0-001 - Project Skeleton

## Goal

Initialize the repository skeleton only. Do not implement Approval Agent, approval business logic, Contract Agent, Router, Handoff, or MCP in this task.

## Source of truth

- `docs/V2.1-technical-design.md`
- `docs/V3.0-codex-execution-manual.md`
- `CODEX.md`

## In scope

1. Create `backend/` using Python + FastAPI + uv.
2. Create `frontend/` using Vue 3 + TypeScript + Vite + pnpm.
3. Add PostgreSQL and Redis to `docker-compose.yml`.
4. Add backend `GET /health` endpoint.
5. Add a minimal accessible frontend shell page.
6. Create only the top-level directories required by V2.1; do not generate large numbers of empty implementation files.
7. Add `.env.example` using placeholders only.
8. Commit `uv.lock` and `pnpm-lock.yaml`.
9. Establish initial lint/test/build commands.

## Out of scope

- Approval domain implementation
- Approval Agent
- Contract Agent
- LangGraph graph implementation
- Real company OA / SSO / knowledge APIs
- MCP
- Evaluation implementation
- Extra databases, queues, or microservices

## Acceptance criteria

- PostgreSQL and Redis start with Docker Compose.
- Backend starts successfully and `/health` returns HTTP 200.
- Frontend starts successfully and renders the shell page.
- Backend lint/test baseline runs successfully.
- Frontend typecheck/build baseline runs successfully.
- Repository contains no real secret.
- `uv.lock` and `pnpm-lock.yaml` exist.

## Execution instructions to Codex

Before editing, output:

1. Implementation plan.
2. Files/directories you expect to add or modify.
3. Risks or ambiguities you found.

Then implement the task, actually run the acceptance commands, and finish with the Task Completion Report required by `CODEX.md`.
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