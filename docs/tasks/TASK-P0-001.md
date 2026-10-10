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
