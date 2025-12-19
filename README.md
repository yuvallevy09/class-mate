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
- **Course chat (course-scoped, persisted)**:
  - Course chat endpoint: `POST /api/v1/courses/{courseId}/chat`
  - Backend persists **conversations + messages** in Postgres and exposes:
    - `GET /api/v1/courses/{courseId}/conversations`
    - `GET /api/v1/conversations/{conversationId}/messages`
    - `DELETE /api/v1/conversations/{conversationId}`
  - Backend generates responses via **Gemini (LangChain)** when `GOOGLE_API_KEY` or `GEMINI_API_KEY` is configured.
- **RAG (PDF → per-course vector index → citations)**:
  - File-backed **PDF** course contents are indexed into a persisted per-course **Chroma** store on disk.
  - Indexing is triggered automatically when you create a content item with an attached file (`POST /courses/{courseId}/contents`), and can also be triggered manually.
  - Chat uses retrieval **best-effort** (injects retrieved excerpts into the prompt when an index exists) and returns `citations[]` with snippet + metadata (e.g. `original_filename`, `page`, `score`).

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
  - Local-first RAG index persisted to disk (`.rag_store/` by default)

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
  - `VITE_CHAT_ENABLED=true` (enables the UI chat input; backend chat requires a Gemini API key)

Configure backend chat keys (optional, only needed for chat replies):

- Set `GOOGLE_API_KEY` (preferred) or `GEMINI_API_KEY` in `backend/.env`

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

## RAG: indexing and debugging (dev)

RAG is **per-course** and stores a persisted Chroma index under:

- `backend/.rag_store/users/{userId}/courses/{courseId}/` (by default; configurable via `RAG_STORE_DIR`)

### How indexing works

- **Upload flow**:
  - Frontend requests a presigned URL: `POST /api/v1/uploads/presign`
  - Browser uploads directly to S3/MinIO via the presigned `PUT`
  - Frontend creates the content record with `file_key` + file metadata: `POST /api/v1/courses/{courseId}/contents`
- **Indexing trigger**: when a content item with `file_key` is created and `RAG_ENABLED=true`, the backend schedules indexing in-process via `BackgroundTasks`.
- **Currently indexed formats**: PDFs only (text is extracted with `pypdf` and chunked; no OCR).

### Requirements

- **S3 configured** (at minimum `S3_BUCKET`), because indexing fetches the uploaded PDFs from object storage.
- **Embeddings configured**:
  - **Gemini embeddings (default)**: set `GOOGLE_API_KEY` or `GEMINI_API_KEY` and ensure quota/billing allows embeddings.
  - **Local embeddings**: set `RAG_EMBEDDINGS_PROVIDER=hf` (uses `sentence-transformers`, downloads the model on first run).

### Debug endpoints

- `GET /api/v1/courses/{courseId}/rag/status` — sanity info (enabled, index exists, PDF count, etc.)
- `POST /api/v1/courses/{courseId}/rag/reindex` — rebuild/refresh the index in the background
- `GET /api/v1/courses/{courseId}/rag/query?q=...&top_k=4` — retrieval-only debug (no LLM)
- `POST /api/v1/courses/{courseId}/rag/clear` — dev helper: delete the on-disk index for the course

---

## Tests

Backend tests live in `backend/tests/` and cover core flows (auth, courses, chat contract, migrations, validation guards).

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