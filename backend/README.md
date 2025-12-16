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

## Apply migrations (dev)

```bash
cd backend
uv run alembic upgrade head
```

## Seed a dev user (for login)

This project supports signup via `POST /api/v1/auth/signup` (and the frontend signup page).

If you prefer seeding a dev user directly in the DB (useful for quick local testing), run:

```bash
cd backend
uv run python scripts/create_user.py --email you@example.com --password pw --display-name "Dev User"
```

## Run (dev)

```bash
cd backend
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port ${PORT:-3001}
```

## Local dev notes (CORS + cookies + CSRF)

- **CORS**: set `CORS_ORIGINS` to the exact Vite origin (e.g. `http://localhost:5173`). Cookie auth requires `allow_credentials=True` and **cannot** use `*` for origins.
- **Host consistency**: keep your frontend and API calls consistent (prefer `localhost` everywhere). Mixing `localhost` and `127.0.0.1` will break cookie-based auth.
- **Cookies/CSRF**: for local HTTP dev, the default `.env` settings are intended to work (`COOKIE_SECURE=false`, `COOKIE_SAMESITE=lax`, CSRF enabled). The frontend must send `credentials: "include"` and include `X-CSRF-Token` on unsafe requests.

## Healthcheck

- `GET /health`
- `GET /health/db` (requires Postgres running)


