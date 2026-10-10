# Enterprise Agent

An independently reconstructed local demo based on real business experience. It contains no company source code, data, or internal APIs. All future demo data will be fictional.

## Phase 0

This repository currently contains the project skeleton: a FastAPI health endpoint, a minimal Vue shell, and local PostgreSQL/Redis services.

### Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node.js and pnpm
- Docker Compose

### Start the backend

```powershell
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

Visit `http://127.0.0.1:8000/health`.

### Start the frontend

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

### Start PostgreSQL and Redis

Copy `.env.example` to `.env` and replace the local placeholder password, then run:

```powershell
docker compose up -d postgres redis
```

The compose file also has local defaults so the services can start without a `.env`; change the placeholder before exposing ports beyond your machine.

### Quality checks

```powershell
cd backend
uv run ruff check .
uv run ruff format --check .
uv run pytest

cd ../frontend
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

