# Backend (FastAPI)

Async FastAPI backend using `pyproject.toml` + `uv`.

For the architecture in depth (with diagrams), see [`../docs/`](../docs/README.md) — deep dives on data, video processing, the RAG/AI pipeline, streaming UX, and security.

## Prereqs

- Python 3.12+ recommended
- [`uv`](https://github.com/astral-sh/uv) installed
- `ffmpeg` / `ffprobe` on `PATH` (audio + thumbnail extraction for uploaded videos)
- Docker (for local Postgres + MinIO)

## Setup

```bash
cd backend
cp env.example .env
uv sync
```

## Start Postgres + MinIO (dev)

```bash
cd backend
docker compose up -d
```

This starts:

- **Postgres** on `localhost:5433` (container port 5432). The bundled image enables **pgvector** and full-text search, which back the retrieval (RAG) layer — no separate vector store is needed.
- **MinIO** (S3-compatible object storage) on `localhost:9000` (API) and `localhost:9001` (console), as a local alternative to Amazon S3. A `classmate` bucket is created automatically by `minio-init`.

Object storage is **Amazon S3**. Leaving `S3_ENDPOINT_URL` empty (as in `env.example`) targets AWS directly; point it at `http://localhost:9000` to use the local MinIO container instead. Set `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, and `S3_REGION` accordingly.

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
./run.sh
```

`run.sh` wraps the uvicorn command with auto-reload and respects `HOST`/`PORT` env overrides (defaults `0.0.0.0:3001`). The equivalent long form is:

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port ${PORT:-3001}
```

## AI / transcription keys (optional)

See `env.example` for the full list. The most relevant keys:

- **Chat provider** — `LLM_PROVIDER=gemini` (dev default; needs `GOOGLE_API_KEY`) or `anthropic` (needs `ANTHROPIC_API_KEY`). Per-task model tiering is on by default via `MODEL_ROLES_ENABLED`; see `app/ai/model_roles.py`.
- **Embeddings** — `OPENAI_API_KEY` is used for retrieval embeddings (`RAG_EMBEDDING_MODEL`, 1536 dims; the dimension is fixed in the pgvector schema).
- **Video transcription** — `RUNPOD_API_KEY` + `RUNPOD_ENDPOINT_ID` point at a Runpod serverless faster-whisper endpoint; required only to transcribe uploaded videos.

Chat works without any key in tests, but real replies require a configured provider.

## Local dev notes (CORS + cookies + CSRF)

- **CORS**: set `CORS_ORIGINS` to the exact Vite origin (e.g. `http://localhost:5173`). Cookie auth requires `allow_credentials=True` and **cannot** use `*` for origins.
- **Host consistency**: keep your frontend and API calls consistent (prefer `localhost` everywhere). Mixing `localhost` and `127.0.0.1` will break cookie-based auth.
- **Cookies/CSRF**: for local HTTP dev, the default `.env` settings are intended to work (`COOKIE_SECURE=false`, `COOKIE_SAMESITE=lax`, CSRF enabled). The frontend must send `credentials: "include"` and include `X-CSRF-Token` on unsafe requests.

## Healthcheck

- `GET /health`
- `GET /health/db` (requires Postgres running)


