# ClassMate

ClassMate is a study companion for **lecture videos**. You upload a course's lectures, ClassMate transcribes them, summarizes them, and lets you chat with an AI assistant that answers from what was actually said in class — with citations that jump you to the exact moment in the video.

Instead of a generic chatbot, the assistant is grounded in your lectures, enabling questions like:

- “Where did we cover matrix multiplication?”
- “Can you compare the proof for PCA in this lecture and the one in lecture 6?”
- “(While viewing the lecture) Can you explain what the prof said in a more intuitive way?”

> **Scope today:** ClassMate handles **video lectures only**. Uploads are restricted to `video/*` (the presign gate; finalize re-checks any supplied MIME type), the DB constrains content to `category = 'media'`, and the UI flow is video-only. (A generic course-contents API layer exists underneath, built for future non-video content.) There is no PDF/notes/slides ingestion in this version — see the [Roadmap](#roadmap).

---

## What's implemented

### Core product

- **Authentication (cookie-based)**: signup / login / logout / refresh, with server-side refresh sessions (rotating, revocable).
- **Courses**: create / list / view / delete, per-user ownership enforced on every route.
- **Video lectures — upload → transcription → AI understanding**:
  - Direct-to-S3 upload via presigned `PUT`, then an atomic server-side **finalize** that creates the lecture record and (optionally) kicks off processing.
  - A background pipeline extracts audio + a thumbnail with **ffmpeg**, transcribes the audio with **faster-whisper on Runpod serverless**, and stores timestamped transcript **segments**.
  - For each lecture, an AI **title, description, and timestamped summary** are generated; a course-level **summary** is refreshed as lectures complete.
  - Optional **semantic chapterization** (off by default — see [LLM configuration](#llm-configuration)).
- **AI chat (grounded, streaming, persisted)**:
  - Two surfaces: **course-wide** chat and **per-lecture** chat (separate conversation spaces). Per-lecture chat is **viewing-context aware** — it knows which lecture and timestamp you're watching.
  - Responses **stream over Server-Sent Events** (thinking → status → citations → answer), with a non-streaming endpoint as a fallback.
  - **Conversations and messages are persisted** in Postgres; each surface has its own conversation history (including a per-lecture conversation switcher).
  - **Citations** are rendered as inline pills that deep-link to the exact timestamp in the player.
- **Retrieval (RAG)**: lecture transcripts are chunked and indexed in **Postgres** for hybrid (lexical + vector) retrieval. The retrieval corpus is **transcripts only**.

### Security baseline

- **Ownership**: every course/lecture/conversation route verifies the authenticated user owns the resource.
- **Cookie auth + CSRF**: HTTP-only access/refresh cookies; refresh tokens are stored only as keyed HMAC hashes and rotate on use. CSRF is a double-submit cookie requiring `X-CSRF-Token` on unsafe methods.
- **CORS for cookie auth**: explicit origins with `allow_credentials=true` (no wildcard).

> **Not yet implemented / good to know:** there is no application-level rate limiting; model "thinking" is streamed live but not persisted (it's gone on reload); chapters are off by default; and the dev `JWT_SECRET` must be overridden in production.

---

## Architecture

A monorepo with two apps:

- **`frontend/`** — Vite + React 18 SPA
  - React Router (custom page resolver) · TanStack Query for server state
  - TailwindCSS + shadcn/ui (Radix) · `react-markdown` + KaTeX for answers/summaries with math · framer-motion
- **`backend/`** — Async FastAPI
  - SQLAlchemy (async) + Alembic
  - **Postgres** for persistence **and** retrieval — `pgvector` (vector search) + `pg_textsearch` (BM25), both built into the bundled Postgres image
  - **Amazon S3** for uploads (MinIO available locally)
  - **ffmpeg** for audio/thumbnail extraction; **Runpod** serverless (faster-whisper) for transcription
  - **DSPy** (+ LangChain) for the AI pipelines (chat cascade, chapterization, summaries)

### Request flow (high level)

1. Frontend fetches a CSRF cookie (`GET /api/v1/auth/csrf`) on boot.
2. Authenticated requests are cookie-based (`credentials: "include"`); unsafe methods include `X-CSRF-Token`.
3. The client retries once on a 401 by calling `/auth/refresh` (non-auth endpoints only).
4. Chat answers stream over SSE; the UI renders status, thinking, citations, and answer tokens as they arrive.

**For the architecture in depth (with diagrams), see [`docs/`](docs/README.md) — five deep dives on data, video processing, the RAG/AI pipeline, streaming UX, and security.**

---

## Local development

### Prerequisites

- **Node.js** (frontend)
- **Python 3.12+** and **uv** (backend)
- **Docker** (local Postgres + MinIO)
- **ffmpeg / ffprobe** on `PATH` (audio + thumbnail extraction)
- **A Runpod faster-whisper endpoint** (only needed to transcribe videos)
- API keys as needed (see [LLM configuration](#llm-configuration))

### 1) Backend

```bash
cd backend
cp env.example .env
uv sync
docker compose up -d        # Postgres (localhost:5433) + MinIO (localhost:9000 / console :9001)
uv run alembic upgrade head
./run.sh                    # API on :3001 (auto-reload). Override with PORT=4000 ./run.sh
```

Health checks: `GET /health`, `GET /health/db`.

The bundled Postgres image enables **pgvector** and **pg_textsearch**; MinIO auto-creates a `classmate` bucket. (From the repo root, `npm run dev:backend` is equivalent to `./run.sh`.)

### 2) Frontend

```bash
cd frontend
npm install
cp env.example .env.local   # set VITE_API_URL=http://localhost:3001, VITE_CHAT_ENABLED=true
npm run dev                 # Vite on :5173
```

`VITE_CHAT_ENABLED=true` enables the course-chat composer (the in-lecture chat is always enabled). `VITE_UPLOAD_MAX_SIZE_MB` is a client-side pre-check (plus UI hint) — the backend enforces the real limit via `UPLOAD_MAX_SIZE_BYTES`.

### 3) Convenience scripts (repo root)

`npm run dev:frontend` · `npm run dev:backend` · `build:frontend` · `lint:frontend` · `install:backend` (see root `package.json`).

---

## LLM configuration

ClassMate uses **three model providers**, configured in `backend/.env`:

| Use | Provider | Default model | Key |
|-----|----------|---------------|-----|
| Chat (router, clarify, answer, title) | Anthropic | `claude-haiku-4-5` | `ANTHROPIC_API_KEY` |
| Chat (retrieval query, cited answer), lecture summary | Anthropic | `claude-sonnet-4-6` | `ANTHROPIC_API_KEY` |
| Chapters, course summary | Gemini | `gemini-2.5-flash` | `GOOGLE_API_KEY` |
| Retrieval embeddings | OpenAI | `text-embedding-3-small` (1536-dim) | `OPENAI_API_KEY` |

**How provider selection works:**

- `LLM_PROVIDER` (default `gemini`) sets the global/fallback chat model — `gemini` (dev) or `anthropic` (prod switch).
- `MODEL_ROLES_ENABLED` (default `true`) tiers each pipeline step by job per the table above (mapping lives in `app/ai/model_roles.py`). **Any tier whose provider key is unset falls back to the global `LLM_PROVIDER` model.** So a dev box with only `GOOGLE_API_KEY` runs the whole chat pipeline on Gemini Flash — it works, but the per-task tiering only fully applies when both `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY` are set (`OPENAI_API_KEY` is embeddings-only and plays no part in tiering).
- **Embeddings are independent of the chat provider.** Without `OPENAI_API_KEY`, transcripts are still indexed for lexical (BM25) search, but the vector leg is skipped (the lecture is marked `done_no_embeddings`). The 1536-dim is fixed in the pgvector schema; changing embedding models to another dimension needs a migration + reindex.

**Other AI flags:**

- `CHAPTERS_ENABLED` (default `false`) — when off, each lecture gets a single "Full Lecture" chapter with no LLM call. Turn on for real semantic chapters.
- `DSPY_TRACING_ENABLED` (default `false`) — dev-only MLflow autolog for DSPy (needs the `mlflow` dev dependency).

---

## Configuration notes

- **Object storage is Amazon S3.** Set `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`; leave `S3_ENDPOINT_URL` empty to target AWS. For fully-local dev, set `S3_ENDPOINT_URL=http://localhost:9000` to use the bundled MinIO (console at `:9001`; default creds `minioadmin` / `minioadmin`).
- **Upload size**: `UPLOAD_MAX_SIZE_BYTES` defaults to **1 GiB** (sized for lecture videos); keep the frontend's `VITE_UPLOAD_MAX_SIZE_MB` pre-check in sync if you change it.
- **CORS**: set `CORS_ORIGINS` to the exact Vite origin (e.g. `http://localhost:5173`); cookie auth can't use a wildcard.
- **Host consistency**: prefer `localhost` everywhere (don't mix with `127.0.0.1`) or cookie auth can break.
- **DB port**: the Docker Postgres is published on **5433**; point `DATABASE_URL` at `127.0.0.1:5433` (as in `env.example`).

---

## API surface (v1)

All routes are under `/api/v1` and require cookie auth unless noted. The canonical client flows are marked; a few raw endpoints exist as escape hatches.

- **Auth** (public): `GET /auth/csrf`, `POST /auth/{login,signup,refresh,logout}`
- **Users**: `GET /users/me`, `DELETE /users/me`
- **Courses**: `GET|POST /courses`, `GET|DELETE /courses/{id}`
- **Lectures (video)**: `POST /courses/{id}/videos` *(canonical: atomic content+asset, optional transcription kickoff)*, `GET /courses/{id}/video-assets`, `GET /video-assets/{id}`, `.../segments`, `.../chapters`, `.../summary`, `POST /video-assets/{id}/transcribe` *(start/retry)*, `POST /courses/{id}/video-assets` *(escape hatch: asset for an existing content row)*
- **Contents (generic library layer)**: `GET|POST /courses/{id}/contents`, `GET|DELETE /contents/{id}`, `GET /contents/{id}/download[-redirect]`
- **Uploads**: `POST /uploads/presign` *(presigned PUT; rejects non-`video/*`)*
- **Chat**: `POST /courses/{id}/chat-v2` *(non-streaming)*, `POST /courses/{id}/chat-v2/stream` *(SSE)*
- **Conversations**: `GET /courses/{id}/conversations[?video_asset_id=]`, `GET /conversations/{id}/messages`, `DELETE /conversations/{id}`
- **Retrieval debug** (read-only): `GET /courses/{id}/rag/status`, `.../rag/query` *(semantic)*, `.../rag/lexical_query` *(BM25)*

---

## Tests

Backend tests live in `backend/tests/` (37 files) covering auth, courses, the video assets API and S3 cleanup, the chat-v2 SSE/streaming/thinking/citations contract, viewing-context and per-lecture conversations, hybrid/explicit/Postgres retrieval, the (mocked) transcription pipeline, chapters, lecture artifacts, course summary/info, migrations, validation guards, and model-role resolution.

```bash
cd backend
uv run pytest
```

DB-backed tests run against a `<db>_test` database (auto-created + migrated by `tests/conftest.py`); if Postgres is unreachable, those tests skip themselves so the suite still runs.

---

## Roadmap

- **Answer feedback / ratings (in progress)**: an opt-in "Help ClassMate get better" toggle plus 1–5 star ratings on answers, to collect DSPy training data. The frontend UI exists behind `VITE_FEEDBACK_ENABLED` (off by default, currently backed by a localStorage stub); the backend has only the `FEEDBACK_ENABLED` flag so far — the feedback endpoints and DB columns are not built yet.
- **Index more than transcripts**: bring uploaded PDFs / slides / notes (DOCX/PPTX, plaintext) into the retrieval corpus, with OCR for scanned slides and layout-aware chunking.
- **Surface chapters in the player UI** and enable semantic chapterization by default.
- **Persist model reasoning** so the "thinking" panel survives reloads.
- **Move processing to a real worker/queue** (out of the request process) for large courses, with rate limiting.
- **Multilingual support** and improved cross-lecture search/discovery.

---

## Project structure (quick map)

- `frontend/` — React app: pages in `src/pages/` (Courses, CourseOverview, CourseContent, VideoPlayer, CourseChat); API client in `src/api/` (`chatStream.js` for SSE, `videoAssets.js`); chat UI in `src/components/chat/`.
- `backend/` — FastAPI app: routes in `app/api/v1/`; AI pipelines in `app/ai/` (DSPy chat/chapters, teaching assistant, summaries); retrieval in `app/rag/` (hybrid / pgvector / explicit); services in `app/services/` (transcription, chapters, lecture artifacts, citations); models in `app/db/models/`; migrations in `alembic/`.

For backend setup specifics, see [`backend/README.md`](backend/README.md).
