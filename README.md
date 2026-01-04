# ClassMate

ClassMate is a learning companion that helps students organize academic materials and get context-aware help exactly where they need it.

Users can create courses, upload and manage lecture slides/PDFs/other resources, and interact with an AI assistant that is scoped to a specific course. Instead of a generic chatbot, ClassMate is designed to understand your materials—enabling questions like:

- “Where did we cover matrix multiplication?”
- “Based on previous exams, can you come up with new exam questions for me to practice?”

---

## What’s implemented so far

### Core product features

- **Authentication (cookie-based)**: signup/login/logout with refresh sessions.
- **Course management**: create/list/view/delete courses (per-user ownership enforced).
- **Course content library**:
  - Create/list/delete content items per course and category (notes, exams, media, etc.).
  - Optional file attachment metadata stored alongside content.
- **File uploads (S3-compatible, presigned URLs)**:
  - Backend issues **presigned PUT** URLs; browser uploads directly to object storage.
  - Backend can generate **presigned download** links for attached files.

### Security & privacy baseline

- **Ownership guarantees**: all course/content APIs verify the authenticated user owns the target course/content.
- **Cookie auth + CSRF protection**:
  - HTTP-only access/refresh cookies.
  - CSRF middleware (double-submit cookie) requiring `X-CSRF-Token` for unsafe methods.
- **CORS configured for cookie auth**: origins are explicit and `allow_credentials=true` is enabled.

---

## Architecture

This repo uses a simple monorepo layout with two apps:

- **`frontend/`** — Vite + React SPA
  - React Router for pages
  - TanStack React Query for server state
  - TailwindCSS + shadcn/ui for UI components
- **`backend/`** — Async FastAPI API
  - SQLAlchemy (async) + Alembic migrations
  - Postgres for persistence
  - S3-compatible object storage (MinIO in local dev) for uploads
  - (In progress) new pipeline: local ffmpeg extraction + Runpod transcription + BM25 retrieval + DSPy

### Request flow (high level)

1. Frontend boots and fetches a CSRF cookie (`GET /api/v1/auth/csrf`).
2. Authenticated requests are cookie-based (`credentials: "include"`).
3. Unsafe requests (POST/PUT/PATCH/DELETE) include `X-CSRF-Token`.
4. The frontend has a conservative refresh-on-401 retry for non-auth endpoints.

---

## Local development

### Prerequisites

- **Node.js** (for the frontend)
- **Python 3.12+** recommended (for the backend)
- **uv** (Python package manager)
- **Docker** (recommended for local Postgres + MinIO)

### 1) Backend setup

```bash
cd backend
cp env.example .env
uv sync
```

Start Postgres + MinIO:

```bash
cd backend
docker compose up -d
```

Notes:

- Postgres is exposed on **localhost:5433** (container port 5432).
- MinIO S3 API is on **localhost:9000** and console UI is on **localhost:9001**.

Run migrations:

```bash
cd backend
uv run alembic upgrade head
```

Start the API (default port **3001**):

```bash
cd backend
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port ${PORT:-3001}
```

Health checks:

- `GET /health`
- `GET /health/db`

### 2) Frontend setup

```bash
cd frontend
npm install
```

Configure env:

- Copy `frontend/env.example` to `frontend/.env.local` (or `frontend/.env`)
- Set:
  - `VITE_API_URL=http://localhost:3001`
  - (Optional) any other frontend flags you add as you build the new pipeline

Start the frontend (Vite default port **5173**):

```bash
cd frontend
npm run dev
```

### 3) Convenience scripts (from repo root)

```bash
npm run dev:frontend
npm run dev:backend
```

---

## Configuration notes (dev)

- **CORS**: set `CORS_ORIGINS` in `backend/.env` to the exact Vite origin (e.g. `http://localhost:5173`).
- **Host consistency**: prefer `localhost` everywhere (don’t mix `localhost` and `127.0.0.1`) or cookie auth can break.
- **MinIO/S3**:
  - MinIO S3 API: `http://localhost:9000`
  - MinIO console: `http://localhost:9001`
  - Bucket is created automatically as `classmate` by `minio-init` in `backend/docker-compose.yml`.

---

## Note: pipeline in progress

This repo is being migrated to a new stack:
- local `ffmpeg` audio extraction
- Runpod transcription (faster-whisper + whisper-timestamped)
- BM25 retrieval
- DSPy for RAG/agents

---

## Tests

Backend tests live in `backend/tests/` and cover core flows (auth, courses, migrations, validation guards).

```bash
cd backend
uv run pytest
```

---

## Roadmap (planned)

- **Richer citations UI**: show citations in the chat UI (and deep-link to downloads/pages).
- **More file types**: ingestion beyond PDFs (DOCX/PPTX, plaintext notes, etc.).
- **Better PDF understanding**: OCR for scanned slides, layout-aware chunking, improved metadata extraction.
- **Background workers**: move indexing out of request process (queue + worker) for large courses.
- **Richer course material understanding**: lecture segmentation, timestamped references, metadata extraction.
- **Multilingual support** and improved search/discovery across content.

---

## Project structure (quick map)

- `frontend/` — React app (pages in `frontend/src/pages/`, API client in `frontend/src/api/`)
- `backend/` — FastAPI app (routes in `backend/app/api/v1/`, models in `backend/app/db/models/`, migrations in `backend/alembic/`)

For backend-specific notes (DB, Docker, seeding users), see `backend/README.md`.