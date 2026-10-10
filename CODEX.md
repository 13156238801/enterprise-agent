# CODEX.md

This repository implements the architecture defined in:

- `docs/V2.1-technical-design.md`
- `docs/V3.0-codex-execution-manual.md`

These documents are the source of truth unless an approved ADR explicitly changes them.

## Mandatory working contract

1. Read the V2.1 and V3.0 documents before making changes.
2. Work on exactly one approved TASK or one explicitly approved task group at a time.
3. Do not invent real company OA, SSO, knowledge-base, employee, contract, or internal APIs. Use the approved Adapter abstraction plus local/mock implementation.
4. Agent business actions must go through `ToolExecutor`. Do not let an Agent directly call repositories or mutate the database.
5. Authenticated identity and roles come from server-side auth/task context. Never trust LLM-generated `user_id`, `role`, or permission claims.
6. HIGH-risk actions require structured Human-in-the-loop confirmation. Plain user text such as “确认” is not sufficient authorization by itself.
7. Write operations must implement idempotency where required by V2.1.
8. Every Tool must declare input/output schema, risk level, required permissions, timeout, retry policy, idempotency policy, and allowed agents.
9. Do not silently change public APIs, database schema, approval state machine, Tool contracts, auth model, directory architecture, or framework choices.
10. Architecture deviations require an ADR under `docs/adr/` and explicit reviewer approval before implementation.
11. Never hard-code secrets. Only placeholders may be committed in `.env.example`.
12. Every database schema change requires an Alembic migration.
13. Preserve passing tests. If behavior intentionally changes, explain the reason and update tests deliberately.
14. Do not claim a test or command passed unless it was actually run.
15. At task completion provide a Task Completion Report containing changed files, decisions, commands run, tests, migrations, API changes, security impact, deviations, limitations, and suggested next task.

## Architecture invariants

- API Router -> Application/Domain Service -> Repository/Adapter -> DB/External System.
- Agent -> ToolExecutor -> Tool Handler -> Adapter/Domain Service.
- API routers do not own approval state-machine rules.
- Agents do not import SQLAlchemy repositories.
- Approval history is append-only.
- `acting_user_id` and roles are injected by server context, not selected by the model.
- HIGH-risk actions cannot bypass confirmation.
- Structured business state is authoritative; frontend and Agent must not infer business success from assistant free text.
- Prompts are versioned under `backend/app/prompts/` and prompt changes require relevant evaluation.
- Agent loops require explicit termination/max-step protection.

## Required completion format

```md
## TASK Completion Report
- TASK ID:
- Status: DONE / BLOCKED / PARTIAL
- Changed files:
- Behavior implemented:
- Architecture decisions:
- Commands actually run:
- Tests passed:
- Tests failed:
- DB migrations:
- API changes:
- Security considerations:
- Deviations from V2.1:
- Known limitations:
- Suggested next task:
```
