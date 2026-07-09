# ClassMate — Code Report

**Author:** Yuval Levy

## ClassMate Overview

ClassMate is a study companion for **lecture videos**: upload a course's lectures, and it transcribes, summarizes, and indexes them so a student can chat with an AI assistant that answers _from what was actually said in class_ — with citations that jump to the exact moment in the video.

## Contents

1. [Data model](#data-md)
2. [Video processing](#video-processing-md)
3. [Teaching assistant: RAG pipeline & AI orchestration](#rag-and-ai-md)
4. [Future direction: the feedback → GEPA loop](#coming-soon-md)

---

### Tech Stack

- **Frontend** — Vite + React 18 SPA; TanStack Query; TailwindCSS + shadcn/ui; `react-markdown` + KaTeX; SSE chat streaming.
- **Backend** — async FastAPI; SQLAlchemy (async) + Alembic; **Postgres** for persistence _and_ retrieval (`pgvector` + `pg_textsearch`); **Amazon S3** for uploads; **ffmpeg** + **Runpod** (faster-whisper) for transcription; **DSPy** for the AI pipelines.
- **Models** — three providers, tiered per task: Anthropic (haiku and sonnet for router/answers/lecture summaries), Gemini (flash 2.5 for chapters/course summary), OpenAI (embeddings).

---

## Data model

### Where the data lives

A one-line role for each table, as a quick reference:

| Table                 | Role                                                                                            |
| --------------------- | ----------------------------------------------------------------------------------------------- |
| `users`               | Identity + credentials; everything in the system belongs to a user.                             |
| `refresh_sessions`    | Stores a hashed token and a link to the token that replaced it.                                 |
| `courses`             | A course - groups its lectures and carries the AI-written course overview.                      |
| `course_contents`     | Holds the S3 Key.                                                                               |
| `video_assets`        | A lecture - processing status, extracted audio/thumbnail, and its AI title/description/summary. |
| `transcript_segments` | Segments produced by faster-whisper.                                                            |
| `content_chunks`      | What the AI actually searches - grouped segments.                                               |
| `chat_conversations`  | Chat threads — cross course + video chats.                                                      |
| `chat_messages`       | Each message in a thread, plus the sources the AI cited and its reasoning.                      |

### The entity graph

```mermaid
erDiagram
    USERS ||--o{ COURSES : owns
    USERS ||--o{ REFRESH_SESSIONS : "has sessions"

    COURSES ||--o{ COURSE_CONTENTS : contains
    COURSES ||--o{ VIDEO_ASSETS : contains
    COURSES ||--o{ TRANSCRIPT_SEGMENTS : "scopes"
    COURSES ||--o{ CONTENT_CHUNKS : "scopes"
    COURSES ||--o{ CHAT_CONVERSATIONS : "scopes"

    COURSE_CONTENTS ||--o| VIDEO_ASSETS : "1:1 (content_id)"
    COURSE_CONTENTS ||--o{ CONTENT_CHUNKS : "source row"

    VIDEO_ASSETS ||--o{ TRANSCRIPT_SEGMENTS : "transcribed into"
    VIDEO_ASSETS ||--o{ VIDEO_CHAPTERS : "chapterized into"
    VIDEO_ASSETS ||--o{ CONTENT_CHUNKS : "chunked into"
    VIDEO_ASSETS ||--o{ CHAT_CONVERSATIONS : "anchored to"

    VIDEO_CHAPTERS ||--o{ CONTENT_CHUNKS : "labels (SET NULL)"

    CHAT_CONVERSATIONS ||--o{ CHAT_MESSAGES : "holds turns"

    USERS {
        int id PK
        string email UK
        string hashed_password
        string display_name
        bool is_active
    }
    REFRESH_SESSIONS {
        uuid id PK
        int user_id FK
        string token_hash UK "HMAC-SHA256, never raw"
        datetime expires_at
        datetime revoked_at
        uuid replaced_by_id FK "self-ref, SET NULL"
    }
    COURSES {
        uuid id PK
        int user_id FK
        string name
        text ai_summary "+ generated_at / error"
    }
    COURSE_CONTENTS {
        uuid id PK
        uuid course_id FK
        string file_key "S3 key"
        string mime_type
    }
    VIDEO_ASSETS {
        uuid id PK
        uuid course_id FK
        string status "uploaded -> done"
        text ai_summary "+ title / description"
    }
    TRANSCRIPT_SEGMENTS {
        uuid id PK
        uuid video_asset_id FK
        uuid course_id FK
        float start_sec
        float end_sec
        text text
        string language_code
    }
    VIDEO_CHAPTERS {
        uuid id PK
        uuid video_asset_id FK
        int chapter_index
        float start_sec
        string title
        int artifact_version "+ source_hash / model_id"
    }
    CONTENT_CHUNKS {
        uuid id PK
        uuid course_id FK
        uuid content_id FK
        uuid video_asset_id FK "nullable"
        uuid chapter_id FK "nullable, SET NULL"
        text text
        vector embedding "1536, nullable"
        jsonb metadata
    }
    CHAT_CONVERSATIONS {
        uuid id PK
        uuid course_id FK
        uuid video_asset_id FK "nullable = course chat"
        string title "nullable"
        datetime last_message_at
    }
    CHAT_MESSAGES {
        uuid id PK
        uuid conversation_id FK
        string role "user | assistant"
        text content
        jsonb citations "nullable"
        text thinking "nullable"
    }
```

---

### Ownership and cascade rules

Deleting a user erases everything they own, in one cascade. The FKs are wired so the database does the cleanup — there is no application-level "delete all the children" code to keep in sync.

```mermaid
flowchart TD
    U[users] -->|CASCADE| C[courses]
    U -->|CASCADE| RS[refresh_sessions]
    C -->|CASCADE| CC[course_contents]
    C -->|CASCADE| VA[video_assets]
    C -->|CASCADE| TS[transcript_segments]
    C -->|CASCADE| CH[content_chunks]
    C -->|CASCADE| CONV[chat_conversations]
    CC -->|CASCADE| VA
    CC -->|CASCADE| CH
    VA -->|CASCADE| TS
    VA -->|CASCADE| VCH[video_chapters]
    VA -->|CASCADE| CH
    VA -->|CASCADE| CONV
    CONV -->|CASCADE| MSG[chat_messages]
    VCH -.->|SET NULL| CH
    RS -.->|SET NULL| RS

    classDef setnull fill:#fff3cd,stroke:#d39e00,color:#000;
    class VCH,RS setnull
```

Almost every edge is `CASCADE`. The two exceptions (dashed) are the design decisions worth calling out — both choose to **preserve a row and null a pointer** rather than delete:

| Relationship                                     | Rule         | Why not CASCADE                                                                                                                                                                                                                                                                                                                                                                                                   |
| ------------------------------------------------ | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `video_chapters` → `content_chunks.chapter_id`   | **SET NULL** | Chapters are a _best-effort label_ on a chunk, not its reason for existing. Re-running chapterization deletes and recreates chapter rows; if that cascaded, it would wipe the retrieval corpus every time chapters regenerated. Nulling `chapter_id` lets a chunk outlive the chapter that happened to label it. (See the [chapters note](#a-note-on-chapters) — even dormant, this row is wired into retrieval.) |
| `refresh_sessions` → `replaced_by_id` (self-ref) | **SET NULL** | The rotation chain is auditing metadata, not a hard dependency. Pruning an old session shouldn't fail because a newer one points back at it.                                                                                                                                                                                                                                                                      |

Everything else cascades because the child genuinely has no meaning without its parent: a transcript segment without its video, a chat message without its conversation.

---

### The retrieval corpus: `content_chunks`

```mermaid
flowchart LR
    subgraph row["content_chunks row"]
        T["text (Text)"]
        E["embedding<br/>vector(1536), NULLABLE"]
        M["metadata (JSONB)"]
    end

    T --> BM25["content_chunks_bm25_idx<br/>USING bm25 (text)<br/>text_config = simple"]
    E --> HNSW["content_chunks_embedding_hnsw_idx<br/>USING hnsw (vector_cosine_ops)<br/>m=16, ef_construction=64"]

    BM25 --> LEX[["lexical leg<br/>(BM25 ranking)"]]
    HNSW --> SEM[["semantic leg<br/>(cosine kNN)"]]

    classDef idx fill:#e7f1ff,stroke:#2563eb,color:#000;
    class BM25,HNSW idx
```

Both indexes come from Postgres extensions

- **`pg_textsearch`** provides the `bm25` access method. The index uses the language-agnostic `simple` config — no stemming or stop-word removal, which is the safe default for mixed/unknown lecture languages (although now we only support English).
- **`vector`** (pgvector) provides the `vector(d)` column type and the `hnsw` index. We are using `EMBEDDING_DIMS = 1536`.

---

## Video processing

This is the **write path**: how a raw lecture video becomes a transcribed, chaptered, summarized, and _searchable_ asset. It's the half of the system that runs before anyone asks a question — the [RAG pipeline](#rag-and-ai-md) is the read path that consumes what this produces.

The tables this writes to: `video_assets`, `transcript_segments`, `video_chapters`, `content_chunks`.

> **Where the code lives:** `app/api/v1/uploads.py` (presign), `app/api/v1/video_assets.py` (finalize + transcribe endpoints), and `app/services/transcription.py` (the background task). Chunking is `app/services/transcript_chunk_ingestion.py`; chapters `app/services/video_chapters.py`; AI artifacts `app/services/lecture_artifacts.py` + `course_summary.py`.

---

### 1. The upload handshake

The API **never proxies video bytes**. The browser uploads straight to S3 through a presigned `PUT`, and only then tells the backend "it's there." This keeps large uploads off the application server entirely.

```mermaid
sequenceDiagram
    participant B as Browser
    participant API as FastAPI
    participant S3 as Amazon S3
    participant BG as Background task

    B->>API: POST /uploads/presign<br/>(courseId, filename, contentType, sizeBytes)
    Note over API: owns course? size ok?<br/>contentType video/* ?
    API-->>B: presigned PUT url + key<br/>users/{uid}/courses/{cid}/{uuid}_name

    B->>S3: PUT video bytes (direct)
    S3-->>B: 200 OK

    B->>API: POST /courses/{id}/videos<br/>(source_file_key, kickoffTranscription)
    Note over API: atomic: create course_contents (media)<br/>+ video_assets (1:1), status=uploaded<br/>idempotent on source_file_key
    API->>API: status = extracting_audio
    API->>BG: enqueue transcribe_video_asset
    API-->>B: { content, video_asset }

    BG-->>S3: (later) download, transcode, transcribe…
```

---

### 2. The status lifecycle

```mermaid
stateDiagram-v2
    direction LR
    [*] --> uploaded: finalize
    uploaded --> extracting_audio: kickoff / transcribe
    extracting_audio --> transcribing: audio uploaded to S3
    extracting_audio --> error: ffmpeg failure

    transcribing --> done: chunks + embeddings written
    transcribing --> done_no_embeddings: chunks written, no embeddings
    transcribing --> done_no_index: transcript saved, indexing failed
    transcribing --> error: no segments / Runpod / timeout

    done --> [*]
    done_no_embeddings --> [*]
    done_no_index --> [*]
    error --> [*]
```

---

### 3. Inside the background task

**Part 1 — acquire audio, then transcribe & persist:**

```mermaid
flowchart LR
    subgraph P1[Acquire]
        A[Load asset<br/>guard: source_file_key present] --> B[Download video from S3<br/>streamed to temp file] --> C[ffmpeg: thumbnail frame<br/>adaptive seek, scale 640] --> D[ffmpeg: extract audio<br/>mono / 16 kHz PCM WAV] --> E[Upload audio to S3<br/>+ presign a GET url]
    end
    subgraph P2[Transcribe & persist]
        F[Runpod faster-whisper<br/>runsync OR run+poll] --> G{non-empty<br/>segments?}
        G -->|yes| H[Replace-all<br/>transcript_segments]
    end

    E --> F
    G -->|no| ERR[status = error]
    H --> NEXT([continue to part 2 ▼])

    classDef fatal fill:#f8d7da,stroke:#dc3545,color:#000;
    classDef best fill:#d1e7dd,stroke:#198754,color:#000;
    class ERR fatal
    class C best
```

**Part 2 — index for retrieval, then generate artifacts:**

```mermaid
flowchart LR
    PREV([from part 1: transcript_segments]) --> I
    subgraph P3[Index]
        I[Chapterize<br/>best-effort] --> J[Ingest chunks<br/>content_chunks + embeddings] --> K[Set terminal status<br/>done / no_embeddings / no_index]
    end
    subgraph P4[Artifacts]
        L[Lecture artifacts<br/>title / desc / summary] --> M[Refresh course summary] --> N([done])
    end

    K --> L

    classDef best fill:#d1e7dd,stroke:#198754,color:#000;
    class I,L,M best
```

Green stages are **best-effort** — they're wrapped in `try/except` and can never fail the pipeline. Red is the only kind of fatal: a hard failure before a transcript exists.

| Stage          | What happens                                                                                                                                  | Failure mode                                       |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| Download       | Streams the S3 object to a temp file (never loads the whole video into memory).                                                               | Missing key → `error`.                             |
| Thumbnail      | One ffmpeg frame for the library UI. Seek point adapts — longer videos seek further in to skip black intro frames (`ffprobe` reads duration). | Best-effort; skipped on failure.                   |
| Audio          | ffmpeg normalizes to mono / 16 kHz PCM WAV — the format faster-whisper wants.                                                                 | ffmpeg failure → `error` ("ffmpeg failed").        |
| Upload audio   | WAV goes back to S3 and is presigned, because Runpod fetches it over HTTPS (it can't reach your S3 credentials).                              | Exception → `error`.                               |
| Transcribe     | Runpod serverless faster-whisper. Output parsed into timestamped segments.                                                                    | Job failure / timeout / **no segments** → `error`. |
| Persist        | **Replace-all** for `(video_asset_id, language_code)` — re-running is safe.                                                                   | —                                                  |
| Chapters       | Best-effort; see [below](#chapters-dormant-but-wired).                                                                                        | Never fails the pipeline.                          |
| Ingest         | Chunk + embed into `content_chunks`; decides the terminal `done_*` status.                                                                    | Failure → `done_no_index` (transcript survives).   |
| Artifacts      | AI title / description / summary, only if missing.                                                                                            | Best-effort.                                       |
| Course summary | Re-roll the course-level summary now a new lecture exists.                                                                                    | Best-effort.                                       |

---

### 4. From transcript to retrieval corpus

Persisting `transcript_segments` makes a lecture _playable_. Making it _searchable_ is a separate step: `ingest_video_asset_transcript_to_chunks` rewrites the raw whisper segments into retrieval-sized chunks in `content_chunks`.

```mermaid
flowchart LR
    SEG["transcript_segments<br/>(many short whisper cuts)"] --> CH{group by chapter}
    CH --> DF["duration-first packing<br/>~20–30s target (cap 60s), ~400 tok soft cap<br/>1–2 sentence overlap"]
    DF --> EMB["embed batch<br/>(best-effort)"]
    EMB --> CC["content_chunks<br/>text + embedding? + metadata"]

    classDef best fill:#d1e7dd,stroke:#198754,color:#000;
    class EMB best
```

**Why re-chunk at all?** Whisper emits many short cuts (a few seconds each) — too granular to retrieve against. The ingester packs them into **duration-first** chunks: a chunk flushes once it holds roughly 20–30 seconds of speech (soft cap 60s — an oversized single segment is kept whole), with a soft ~400-token bound to avoid runaway chunks and a 1–2 sentence overlap so a concept split across a boundary is still findable from either side. Duration is the primary axis because it maps to "a coherent thing the lecturer said," and it gives every chunk a clean `[start_sec, end_sec]` for timestamp-deep-linked citations.

Chunks are computed **per chapter** (each chunk gets a `chunk_index_in_chapter`), which is what later powers chapter-scoped neighbor expansion at query time. Embedding is a **best-effort batch**: if `get_embeddings` returns vectors of the right count and dimensionality (1536) they're written, otherwise the chunks land embedding-less and the lecture becomes `done_no_embeddings`. The whole write is **replace-all by `content_id`**, so re-ingesting a lecture cleanly supersedes the old chunks.

Each chunk also carries a small `metadata` JSON blob (`video_asset_id`, `start_sec`/`end_sec`, `language_code`, `title`, `chapter_title`, plus `doc_type`, `source_kind`, and `original_filename`) used to render citations without extra joins.

Semantic chapterization is **feature-flagged off by default** (`CHAPTERS_ENABLED=false`) and not surfaced in the player UI — so as a feature it's dormant.

---

## Teaching assistant: RAG pipeline & AI orchestration

This is the **read path**: how a student's question becomes a grounded, cited answer. It consumes exactly what the last section produced — the `content_chunks` corpus and `transcript_segments` — and turns it into an answer that links back to the moment in the video.

> **Where the code lives:** `app/ai/teaching_assistant.py` is the orchestrator (the DSPy module + cascade). Retrieval is `app/rag/` — `course_retriever.py` (the cascade), `hybrid_retrieve.py` (BM25 + vector + RRF), `explicit_retrieve.py` (timestamp/deictic). Model tiering is `app/ai/model_roles.py` + `llm.py`. Citation post-processing is `app/services/chat_citations.py` + `app/api/v1/chat_v2.py`.

---

### 1. The routed cascade

A chat turn runs through a DSPy module (`TeachingAssistant`) that routes first, then acts:

```mermaid
flowchart TD
    Q[user query<br/>+ course info + history] --> R{route?}
    R -->|clarify| CL[Ask a clarifying question]
    R -->|answer| AN[Answer from<br/>general knowledge]
    R -->|retrieve| GEN[Generate retrieval details<br/>query + lecture routing + target]
    GEN --> RET[[CourseRetriever cascade]]
    RET -->|docs found| ACTX[Answer from context<br/>numeric citations only]
    RET -->|nothing| NOCTX[Honest 'no context' answer]

    classDef terminal fill:#d1e7dd,stroke:#198754,color:#000;
    class CL,AN,ACTX,NOCTX terminal
```

The router classifies into three actions:

- **`answer`** — answerable from general knowledge (definitions, "what's this course about"). No retrieval; saves latency and tokens.
- **`clarify`** — too ambiguous to answer or search. Ask one direct question and stop.
- **`retrieve`** — needs specifics from the lectures. Generate a search query, run the cascade, answer from what came back.

When the router emits anything outside this set, normalization **defaults to `retrieve`** — better to look for evidence than to guess. And if the `retrieve` branch finds nothing, it doesn't fabricate: it routes to an honest "I couldn't find that in the lectures" answer (the prompt is primed with a system note that retrieval came back empty).

Each step is a **DSPy signature** — a typed input/output contract — wrapped in a module:

| Signature                  | Module           | Purpose                                                                       |
| -------------------------- | ---------------- | ----------------------------------------------------------------------------- |
| `RouteQuery`               | `ChainOfThought` | Classify into answer / retrieve / clarify.                                    |
| `AskClarification`         | `Predict`        | Produce a clarifying question.                                                |
| `AnswerWithoutContext`     | `ChainOfThought` | General-knowledge answer.                                                     |
| `GenerateRetrievalDetails` | `ChainOfThought` | Emit a self-contained query, lecture routing, and an optional jump-to target. |
| `AnswerFromContext`        | `ChainOfThought` | Answer using only retrieved docs, with strict numeric citations.              |

We also utilize DSPy's **`streamify`** for token streaming to get the "thinking" effect in the chat.

---

### 2. The retrieval cascade

```mermaid
flowchart TD
    A{slug + timestamp<br/>given & found?} -->|yes| EX([explicit])
    A -->|no| B{routed lectures fit<br/>under char budget?}
    B -->|yes| FL([full_lecture])
    B -->|no| C{hybrid search<br/>on routed lectures?}
    C -->|hits| HY([hybrid])
    C -->|empty & was scoped| D{course-wide<br/>retry?}
    C -->|empty & was course-wide| NONE([none])
    D -->|hits| HCW([hybrid_course_wide])
    D -->|empty| NONE

    classDef ok fill:#d1e7dd,stroke:#198754,color:#000;
    classDef bad fill:#f8d7da,stroke:#dc3545,color:#000;
    class EX,FL,HY,HCW ok
    class NONE bad
```

| Path                 | When it fires                                                               | Strategy                                                                                            |
| -------------------- | --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `explicit`           | The model resolved a `(lecture, timestamp)` target — "in L2 at 27:36…".     | Deterministic window around that moment ([§3](#3-explicit--deictic-retrieval-the-chapters-payoff)). |
| `full_lecture`       | One or a few routed lectures whose full transcript fits a character budget. | Feed the whole transcript — no lossy chunking when it's cheap to read it all.                       |
| `hybrid`             | The routed lectures are too big for full-text; search within them.          | BM25 + vector + RRF, scoped to those lectures ([§4](#4-hybrid-retrieval)).                          |
| `hybrid_course_wide` | A _scoped_ hybrid search came back empty.                                   | Retry across the whole course — rescues a confident-but-wrong lecture pick.                         |
| `none`               | Nothing matched anywhere.                                                   | Caller switches to the honest "no context" answer.                                                  |

---

### 3. Hybrid retrieval

For the `hybrid` paths, two independent legs run over `content_chunks` and get fused:

```mermaid
flowchart LR
    Q[contextualized query] --> LEX[Lexical leg<br/>pg_textsearch BM25<br/>top 20]
    Q --> EMB[embed query]
    EMB --> SEM[Semantic leg<br/>pgvector cosine, HNSW<br/>top 20]
    LEX --> RRF[RRF fuse<br/>k0=60 → top 8]
    SEM --> RRF
    RRF --> NB[Neighbor expansion<br/>±1 chunk in chapter]
    NB --> DOCS[retrieved docs]

    SEM -.->|unavailable| LEXONLY[lexical-only<br/>still expands neighbors]
    LEXONLY -.-> NB
```

- **Lexical leg** — Postgres `pg_textsearch` BM25 over the chunk text. Always runs.
- **Semantic leg** — embed the query (OpenAI `text-embedding-3-small`, 1536-dim) and rank by pgvector cosine distance over the HNSW index. **Best-effort.**
- **RRF fusion** — [Reciprocal Rank Fusion] each leg contributes `1/(k0 + rank)` per hit (`k0=60`), summed across legs, deduped by `chunk_id` (with `id` / content-prefix fallbacks), top 8. RRF needs no score calibration between the two legs — it works purely on ranks, which is what makes mixing BM25 scores with cosine distances clean.
- **Neighbor expansion** — after fusion, pull ±1 neighboring chunk

---

## Future direction: the feedback → GEPA loop

ClassMate's next feature closes the loop between students and the AI pipeline: students **rate answers**, low ratings say **what went wrong**, and that signal becomes training data for **[GEPA](https://dspy.ai/api/optimizers/GEPA/)** — DSPy's reflective prompt optimizer — which rewrites the tutor's prompts and ships them back to production behind a gate. A star rating becomes stage-level training signal.

> **Status: designed and built, not yet shipped.** The frontend ("Help ClassMate learn") lives on this branch behind `VITE_FEEDBACK_ENABLED` (off by default) and currently runs on a localStorage stub. The backend — feedback API, answer snapshots, and the whole optimization harness — exists as work-in-progress on the **`feedback-gepa-backend`** branch.

---

### The loop at a glance

```mermaid
flowchart LR
    RATE[student rates answer<br/>1–5 stars] -->|≤2★ requires<br/>a category| TAG[category tags the<br/>failing pipeline stage]
    RATE --> SNAP[(answer snapshot<br/>full inputs + outputs)]
    TAG --> SNAP
    SNAP --> GEPA[GEPA optimizes<br/>the DSPy prompts<br/>offline]
    GEPA --> GATE{ship gate<br/>+ judge validation}
    GATE -->|passes| ART[versioned artifact,<br/>committed to git]
    GATE -->|fails| GEPA
    ART -->|TUTOR_PROGRAM_ARTIFACT| LIVE[live tutor serves<br/>optimized prompts]
    LIVE --> RATE

    classDef gate fill:#fff3cd,stroke:#d39e00,color:#000;
    class GATE gate
```

---

### 1. Capturing feedback

#### The UX (already built, behind the flag)

- A global **"Help ClassMate learn"** opt-in toggle in the account menu (Navbar). Everything is consent-first — no opt-in, no rating UI, no data capture.
- Under each finished, persisted assistant answer (course chat and video chat): a **1–5 star** rating row. Picking a rating opens a popover — a **rating ≤ 2★ requires a "what went wrong?" category** (that's the credit-assignment signal, so there's no skip button); higher ratings take an optional comment. Submitted feedback collapses to a compact "Thanks — noted ★★★☆☆" with an edit link.
- Ratings only appear on _persisted_ messages — the message id is what feedback and snapshots key on.

---

### 2. Credit assignment: category → stage

The four "what went wrong?" categories map to the pipeline stage that can fix them:

| Category (UI label)                                               | Stage blamed                            |
| ----------------------------------------------------------------- | --------------------------------------- |
| `unnecessary_clarification` — "Asked me to clarify unnecessarily" | **router**                              |
| `wrong_lecture` — "Wrong / missing lecture"                       | **query_generator** (retrieval details) |
| `bad_answer` — "Answer wrong, vague, or too long"                 | **answer** step                         |
| `other` — "Something else"                                        | _none_ — no reliable attribution        |

This mapping lives in a dependency-free module (`app/schemas/feedback.py`) so both the API and the offline optimizer import it without pulling DSPy into the request path. During optimization, the metric routes the student's verbatim comment **only to the blamed predictor** — the router never hears about a bad answer, and vice versa. Untagged feedback (≥3★ or `other`) falls back to heuristics: routing predictors hear about retrieval recall, the answer predictor hears the LLM judge.
