# ClassMate — New Pipeline:

### Accurate incremental plan (updated for DSPy + BM25 + ffmpeg/Runpod)

#### 0) Lock requirements (½ day)
- **Media source**: uploaded video files (recommended) vs external URLs.
- **Timestamp granularity**: segment-level vs word-level (if word-level, add storage now).
- **BM25 engine choice**: SQLite FTS5 (simplest), Postgres FTS, or Tantivy.
- **Where whisper-timestamped runs**: ideally inside the Runpod worker.

---

### 1) OpenAPI-first contract (1 day)
Define/stabilize API shapes *before* internals:
- **Media**: register/upload, start transcription, poll status, fetch segments (and words if needed).
- **RAG (BM25)**: status, reindex, query.
- **Chat**: keep `POST /courses/{courseId}/chat` returning `{ text, citations[], conversationId }` stable.

---

### 2) Add provider flags + clean internal interfaces (½–1 day)
Introduce settings + abstraction points so legacy can coexist:
- `VIDEO_PROVIDER=local` (ffmpeg + Runpod transcription)
- `RETRIEVER_PROVIDER=bm25` (BM25 first; pgvector later if desired)
- `CHAT_PROVIDER=dspy`
Add internal boundaries:
- `transcribe_media(course_id, media_id, ...)`
- `index_course_bm25(course_id, user_id)`
- `bm25_retrieve(course_id, user_id, query, top_k, filters)`
- `dspy_chat(course_id, user_id, history, message)`

---

### 3) Local media asset support (DB + endpoints) (1–2 days)
- **DB**: extend `video_assets` (or add `media_assets`) to support uploaded videos:
  - store `source_file_key`, provider `"local"`, transcription status/job id/error, etc.
- **API**: register local media for a course (linked to an uploaded file).

Keep Bunny paths untouched for now.

---

### 4) ffmpeg extraction pipeline (1–2 days)
- Implement background job:
  - fetch video from S3/MinIO
  - run `ffmpeg` to produce normalized audio
  - optionally store audio back to S3 for retries/debugging

---

### 5) Runpod faster-whisper transcription integration (2–4 days)
- Implement Runpod client:
  - submit job (audio bytes or presigned URL)
  - poll until complete
  - parse response
- Persist output into `transcript_segments` (replace-all per `(media_asset_id, language)`).
- If word-level timestamps are required, persist them too.

---

### 6) whisper-timestamped compliance (1–3 days)
- Ensure the transcription output explicitly comes from **whisper-timestamped**:
  - best: Runpod worker runs whisper-timestamped and returns timestamps in the response format you need.
- Map output cleanly into DB (segment times + optional word times).

---

### 7) Build BM25 retriever + per-course indexing (2–5 days)
This is the “RAG foundation” DSPy will call.
- Index content sources:
  - PDFs (page/chunk text + metadata)
  - transcript segments (start/end + metadata)
- Implement:
  - `bm25_reindex(course)` (background task)
  - `bm25_query(course, q, top_k, filters)` → returns hits + metadata + score
- Keep/implement debug endpoints: `/rag/status`, `/rag/reindex`, `/rag/query`.

Do not delete Chroma yet; keep it behind a flag if you want rollback.

---

### 8) Replace chat engine with DSPy RAG program (2–6 days)
- Implement a DSPy module that:
  - takes `question` (+ optional conversation history)
  - calls BM25 retriever (single-hop first)
  - produces `answer` + references that you convert to your existing `citations[]`
- If your professor wants “agents”:
  - extend to multi-hop retrieval / agent loop (DSPy multi-hop/agent tutorial patterns)
  - keep API stable

Use DSPy’s RAG workflow as guidance ([DSPy RAG tutorial](https://dspy.ai/tutorials/rag/)).

---

### 9) Frontend updates for local media + remove Bunny coupling (1–3 days)
- Replace Bunny-specific video debug page with:
  - upload/register local video
  - show transcription status
  - show segments
  - play local video (HTML5 player) and jump by timestamps
- Chat UI can remain mostly unchanged.

---

### 10) Deprecate legacy (cleanup phase)
Once local transcription + BM25 + DSPy works end-to-end:
- Disable/remove Bunny webhook + Bunny modules + Bunny UI.
- Disable/remove Chroma embedding pipeline (unless you keep it as an optional “later”).
- Remove LangChain chat engine once DSPy is the default.

---

### Minimal “grading-friendly” milestones
- **M1**: OpenAPI finalized + flags in place
- **M2**: upload video → ffmpeg audio → transcript stored
- **M3**: BM25 `/rag/query` works over PDFs + transcripts
- **M4**: DSPy chat uses BM25 and returns citations
- **M5**: legacy Bunny disabled

If you tell me “segment-only vs word-level timestamps” and “video upload vs URL”, I can tighten this further into a checklist with exact endpoint payloads and DB fields.