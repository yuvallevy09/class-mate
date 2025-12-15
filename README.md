# ClassMate

Monorepo layout with separate frontend and backend.

## Structure

- `frontend/`: Vite + React app
- `backend/`: FastAPI (async) API

## Run (frontend)

```bash
cd frontend
npm install
npm run dev
```

### Frontend env

- Copy `frontend/env.example` to `frontend/.env.local` (or `frontend/.env`) and set `VITE_API_URL` to your API origin (e.g. `http://localhost:3001`).

## Run (backend)

```bash
cd backend
cp env.example .env
uv sync
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port ${PORT:-3001}
```

## Root scripts (optional)

```bash
npm run dev:frontend
npm run dev:backend
```

## Build (frontend)

```bash
cd frontend
npm run build
npm run preview
```