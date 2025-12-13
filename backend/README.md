# Backend (FastAPI)

Async FastAPI backend scaffold using `pyproject.toml` + `uv`.

## Prereqs

- Python 3.12+ recommended
- [`uv`](https://github.com/astral-sh/uv) installed

## Setup

```bash
cd backend
cp env.example .env
uv sync
```

## Start Postgres (dev)

```bash
cd backend
docker compose up -d
```

## Run (dev)

```bash
cd backend
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port ${PORT:-3001}
```

## Healthcheck

- `GET /health`
- `GET /health/db` (requires Postgres running)


