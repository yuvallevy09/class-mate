# ClassMate — Poster Content Reference

> Working reference for the final-project poster. Every technical claim below was
> **verified against the source code** on 2026-07-07 (branch `classmate-dev`,
> ignoring the uncommitted `feedback-gepa-backend` work), not just copied from the
> docs. Where a doc and the code could drift, the code wins.
>
> **Main focus of the poster:** this is a **RAG application built with LLMs + DSPy.**
> The retrieval/orchestration pipeline is the intellectual centerpiece — everything
> else (video processing, streaming UX, security) is supporting engineering.

---

## 0. How the winning example posters were built (design brief)

Distilled from the six examples that got good grades — the *software* ones
(Epicure, AeroGlyph, Courses-Reviews, LLM-Hallucinations) are the closest match:

- **Canvas is fixed: 90 × 120 cm, portrait.** Every example uses exactly this.
- **Top 18 cm band is mandatory and unchanged** — cream background, BGU logo top-right,
  "הפקולטה למדעי הטבע" / "המחלקה למדעי המחשב", then project title (100pt),
  students (80pt), `Advisors:` (80pt). Only swap in our own title/names.
- **Everything below the band is ours.** Two-column layout, section boxes each with a
  bold colored title bar.
- **Terse, not paragraphs.** Short bullets. AeroGlyph is almost all diagrams + one-liners.
- **Visuals carry it:** an architecture/flow diagram, a DB-schema diagram, real app
  screenshots, and a **"Technology" panel with framework logos**.
- **Big fonts:** section titles ~70–80pt, body ~44–50pt (read from a distance).

**Verdict for us:** lean AeroGlyph/Epicure style — bold, diagram-heavy, low word count.
Our differentiator vs. those examples is *algorithmic depth* (the cascade), so give the
RAG pipeline the largest, most central real estate.

---

## 1. Header block (fills the fixed top band)

- **Project title:** `ClassMate` — a study companion that lets you **chat with your
  lecture videos.**
  - Subtitle option: *"Grounded, cited answers from what was actually said in class."*
- **Student(s):** Yuval Levy *(confirm/add teammates)*
- **Advisor:** *(fill in — "Advisors: …")*

**One-line elevator pitch (for the top of the body or the abstract box):**
> ClassMate turns a course's lecture videos into a searchable knowledge base: upload
> videos, and it transcribes, summarizes, and indexes them so a student can ask
> questions and get **answers grounded in the lectures — with citations that jump to the
> exact second in the video.** The AI layer is a **routed DSPy cascade over hybrid
> (lexical + vector) retrieval.**

---

## 2. Motivation / Problem  *(section box — like "Background")*

- Students re-watch entire lectures to find one thing. Scrubbing a 90-minute video is slow.
- Generic chatbots (ChatGPT) **don't know your specific course** and **hallucinate** —
  no way to verify an answer against what the lecturer actually said.
- Lecture *video* is the hardest content to search: it's not text, it has no index, and
  the useful unit is "the 40 seconds where she explained backprop," not "the file."

**The gap:** there is no tool that answers course questions **from the lectures
themselves**, and **proves it** by linking back to the moment in the video.

---

## 3. Goals / Objectives  *(section box)*

- Ground every answer in the **course's own lectures** — never general web knowledge when
  a lecture covers it.
- Make every answer **verifiable**: inline citations that **deep-link to the exact
  timestamp** in the player.
- Be **situationally aware**: while watching, "what did she just say?" / "explain that"
  should resolve to the current lecture + playhead.
- Spend LLM tokens intelligently — **cheap models for cheap decisions, flagship models
  only where correctness matters.**
- Degrade gracefully — a missing API key or a failed sub-step should **never** break a turn.

---

## 4. ⭐ The core: RAG + LLMs + DSPy  *(the biggest, most central section)*

This is the poster's centerpiece. Suggested title: **"How an answer is produced"** or
**"Grounded answering: a routed DSPy cascade."**

### 4a. It is NOT retrieve-then-answer — it's a *routed cascade*

A chat turn runs through a **DSPy module (`TeachingAssistant`) that routes first, then acts.**
A router classifies the question into one of three actions (and defaults to `retrieve`
when unsure — better to look for evidence than guess):

| Route | When | What happens |
|---|---|---|
| **`answer`** | Answerable from general knowledge (definitions, "what's this course about") | Answer directly — **no retrieval**, saves latency + tokens |
| **`clarify`** | Too ambiguous to answer or search | Ask **one** direct question and stop |
| **`retrieve`** | Needs specifics from the lectures | Generate a search query → run the retrieval cascade → answer **only** from what came back |

If `retrieve` finds nothing, it does **not** fabricate — it gives an honest
*"I couldn't find that in the lectures"* answer.

> **Poster diagram:** reuse the flowchart in `docs/rag-and-ai.md §1`
> (query → route? → clarify / answer / retrieve → cascade → answer-from-context / no-context).

### 4b. Why DSPy (the "we used a real framework" story)

Each step is a **typed DSPy signature** (input/output contract) wrapped in a module.
**Verified in `app/ai/teaching_assistant.py`:**

| Signature | DSPy module | Job |
|---|---|---|
| `RouteQuery` | `ChainOfThought` | Classify answer / retrieve / clarify |
| `GenerateRetrievalDetails` | `ChainOfThought` | Emit a self-contained search query + lecture routing + optional jump-to target |
| `AnswerFromContext` | `ChainOfThought` | Answer using **only** retrieved docs, strict numeric citations |
| `AnswerWithoutContext` | `ChainOfThought` | General-knowledge answer |
| `AskClarification` | `Predict` | Produce one clarifying question |

DSPy buys three concrete things:
1. **Prompt is separated from control flow** — re-tiering a step or swapping the retriever
   never touches prompt text.
2. **`ChainOfThought` reasoning is captured** and surfaced live as the "thinking" panel.
3. **`dspy.streamify`** gives token-by-token streaming for free.

Bonus talking point: the module is **string-in / string-out and DB-agnostic** (retrieval is
injected), so the whole orchestrator is **unit-testable with a mock retriever + a DSPy `DummyLM`.**

### 4c. The 5-step retrieval cascade — cheapest & most precise first

When the route is `retrieve`, `CourseRetriever` runs a cascade that tries deterministic/precise
strategies before fuzzy/expensive ones. **Verified paths in `app/rag/course_retriever.py`:**

| Path | Fires when | Strategy |
|---|---|---|
| `explicit` | Model resolved a `(lecture, timestamp)` — "in L2 at 27:36…" | Deterministic transcript **window** around that moment |
| `full_lecture` | A few routed lectures fit under a char budget | Feed the **whole transcript** — no lossy chunking when it's cheap |
| `hybrid` | Routed lectures too big → search within them | **BM25 + vector + RRF**, scoped to those lectures |
| `hybrid_course_wide` | A *scoped* hybrid search came back empty | **Widen to the whole course** before giving up — rescues a wrong lecture guess |
| `none` | Nothing matched anywhere | Switch to the honest "no context" answer |

The interesting one is `hybrid_course_wide`: the model scopes to the lectures it *thinks* are
relevant (so a correct lecture isn't starved by global ranking), but if that finds nothing, the
cascade **broadens rather than declaring defeat.**

> **Poster diagram:** reuse the cascade flowchart in `docs/rag-and-ai.md §2`.

### 4d. Hybrid retrieval (the RAG detail judges will ask about)

Two independent legs run over the chunk corpus and get **fused**. **Verified in
`app/rag/hybrid_retrieve.py`:**

- **Lexical leg** — Postgres `pg_textsearch` **BM25** over chunk text. Always runs. Top 20.
- **Semantic leg** — embed the query (**OpenAI `text-embedding-3-small`, 1536-dim**), rank by
  **pgvector cosine** over an **HNSW index** (`m=16, ef_construction=64`). Top 20. Best-effort.
- **RRF fusion** — Reciprocal Rank Fusion: each hit scores `1 / (k0 + rank)` with **`k0 = 60`**,
  summed across legs, deduped, **top 8**. RRF needs no score calibration between BM25 and cosine —
  it works purely on **ranks**, which is what makes mixing the two clean.
- **Neighbor expansion** — after fusion, pull **±1 neighboring chunk within the same chapter**
  (capped at 6 extra) so a hit mid-explanation gets its surrounding context.

> **Poster diagram:** reuse the two-leg → RRF → neighbor-expansion flowchart in `docs/rag-and-ai.md §4`.
> This is a great, compact "here's the RAG algorithm" visual.

### 4e. One job, one model tier (the cost/quality story)

The pipeline talks to **three providers**, and **each call site is sized to its job** —
a flagship for a yes/no routing call would overpay; a cheap model writing cited answers would
underperform. **Verified map in `app/ai/model_roles.py` + defaults in `settings.py`:**

| Role | Tier | Model | Why |
|---|---|---|---|
| `router`, `clarify`, `answer_no_ctx`, `title` | **haiku** | `claude-haiku-4-5` | High-volume / low-stakes decisions |
| `gen_retrieval_params` | **sonnet** | `claude-sonnet-4-6` | **Correctness gate** — search quality decides everything downstream |
| `answer_with_ctx` | **sonnet** | `claude-sonnet-4-6` | **Correctness gate** — citation accuracy |
| `lecture_summary` | sonnet | `claude-sonnet-4-6` | Feeds retrieval, so quality compounds |
| `chapters`, `course_summary` | **flash** | `gemini-2.5-flash` | Input-heavy / display-only, cheapest capable model wins |
| *embeddings* | — | OpenAI `text-embedding-3-small` | Independent of chat provider |

Two things make it robust, not fragile:
- **Safe by construction:** if a tier's provider key is missing, it **falls back to the global
  provider** instead of erroring. A dev box with only a Google key runs the *entire* pipeline on
  Gemini Flash.
- **A real gotcha we handled:** `title` looks like a Flash job but lives on Haiku — Gemini Flash
  spends hidden "thinking" tokens against `max_tokens`, so a tiny title budget truncates to empty.

### 4f. Citations you can trust — numeric by contract, recovered if not

- The `AnswerFromContext` prompt **forbids anything but numeric keys** (`[1]`, `[2]`) — never a
  slug, never a raw timestamp, never a number not in the docs.
- **Recovery:** if a smaller model slips and echoes a slug (`[L1]`), `_normalize_slug_citations`
  rewrites it to the right numeric index before links are formed.
- **Enrichment:** each cited doc becomes a deep link carrying lecture slug, start/end seconds, and
  chapter title → the UI renders a **pill that jumps to the exact second.**

### 4g. Video-mode awareness ("what did she just say?")

When chat is opened over a playing lecture, the turn carries a `ViewingContext` (lecture + timestamp):
- **Deictic anchor** — "The student is watching L2: Backprop at 5:30" is prefixed so "this"/"that"
  resolve correctly.
- **Recent-window merge** — the trailing ~120 s before the playhead is retrieved and merged as a
  citation, so "explain that" works with no explicit query.
- **Soft scoping** — the watched lecture is ensured in the routing set without evicting the model's
  other picks (course-wide questions still reach the whole course).

---

## 5. System architecture  *(section box — needs a diagram)*

One-directional data flow with security wrapping everything:

```
WRITE PATH:  upload → transcribe → chapter → chunk → embed ─┐
                                                            ▼
                                            Postgres (+ pgvector + BM25)
                                                            ▲
READ PATH:   question → routed DSPy cascade + hybrid retrieval → stream answer + citations
             (auth · CSRF · ownership wrap every request)
```

**The headline architectural decision:** **Postgres is the single store for *both*
persistence *and* retrieval** — vectors via `pgvector`, lexical via `pg_textsearch` — so there's
**no separate vector database** (Chroma was removed; see git history). Clean, one-datastore RAG.

> **Poster diagram:** reuse the "system at a glance" flowchart in `docs/README.md`.

---

## 6. Video processing (write path)  *(secondary section)*

How a raw video becomes searchable. Good for a "pipeline" strip of boxes like AeroGlyph's:

`upload → ffmpeg (audio + thumbnail) → faster-whisper on Runpod → timestamped segments →
duration-first chunking → embed → indexed`

Highlights worth one line each:
- **Direct-to-S3 upload** via presigned `PUT` — the API **never proxies video bytes** (large
  uploads stay off the app server).
- **faster-whisper on Runpod serverless** produces timestamped transcript segments.
- **Duration-first chunking:** pack whisper's tiny cuts into **20–60 s chunks** (~400-token soft
  cap, 1–2 sentence overlap) so each chunk is "a coherent thing the lecturer said" with a clean
  `[start, end]` for deep-linkable citations.
- **Best-effort, records-what-it-finished:** terminal states `done` / `done_no_embeddings` /
  `done_no_index` — a course with no embedding key is **lexical-only, not broken.**

> **Poster diagram (optional):** the status state-machine or the pipeline strip from
> `docs/video-processing.md`.

---

## 7. Streaming UX (delivery)  *(secondary — pairs well with a screenshot)*

- Answers **stream over Server-Sent Events** with **progressive disclosure, no waterfalls:**
  `Searching… → thinking streams in → Sources appear → answer types out` — each as soon as it's ready.
- **Citations arrive *before* the answer tokens** (contract), so "Sources" paint while the answer
  is still generating.
- **Typewriter** reveal (~150 chars/s) with sentence cadence; citation pills pop in **atomically**.
- **Dual finalization:** the live message converges *exactly* onto the persisted one, so a
  background refetch never causes a flicker.

---

## 8. Data & infrastructure highlights  *(can fold into architecture)*

- **10-table schema** anchored on a single ownership root (`users`); almost everything scoped by
  `course_id` so retrieval + authorization filter on **one indexed column, no joins.**
- **`content_chunks`** is the retrieval corpus: one row with **both** a BM25 index and a nullable
  `vector(1536)` embedding over the same `text`. Nullable embedding = **vector search is a backfill,
  not a prerequisite.**
- **Idempotency + artifact-lineage** patterns: uploads/transcription retry safely (UNIQUE keys,
  replace-all writes); AI artifacts stored as **value + timestamp + error** triads so a half-finished
  pipeline is queryable.

---

## 9. Security  *(one compact box — don't over-invest)*

- **Cookie-based auth** (HTTP-only JWT — XSS can't read it) + **double-submit CSRF** + per-resource
  **ownership on every route** (returns **404 not 403** → no existence leak).
- **Refresh-token rotation:** raw token never stored (only an HMAC-SHA256 hash); every refresh
  revokes the old session and issues a new one in an auditable chain.

---

## 10. Results / "why it's notable"  *(closing box or woven into the core)*

Frame these as outcomes rather than raw metrics (there's no benchmark table yet):

- **Grounded + verifiable:** answers cite the lectures and **deep-link to the exact second** — not
  a generic chatbot.
- **Answers only what it can support:** honest "not in the lectures" fallback instead of hallucinating.
- **Cost-aware by design:** per-task model tiering across 3 providers; flagship models reserved for
  the two correctness gates.
- **Robust:** every external dependency (embeddings, semantic leg, chapters, transcription) degrades
  gracefully — one job failing never fails the turn.
- **Real engineering:** async FastAPI, 36 backend test files, one datastore for persistence *and*
  retrieval, direct-to-S3 uploads.

*(If you can run a small demo course before submission, a one-line stat like "answers cite the
correct lecture second on N/N demo questions" would strengthen this box a lot.)*

---

## 11. Technology panel  *(logo row — like Epicure's "<TECHNOLOGY/>")*

Verified from `pyproject.toml`, `package.json`, and the docs:

**AI / RAG**
- **DSPy** (`dspy-ai`) — pipeline orchestration & signatures
- **LangChain** — supporting LLM utilities / text splitters
- **Anthropic Claude** — `claude-sonnet-4-6`, `claude-haiku-4-5`
- **Google Gemini** — `gemini-2.5-flash`
- **OpenAI** — `text-embedding-3-small` (embeddings)
- **faster-whisper** (on **Runpod** serverless) — transcription

**Backend**
- **FastAPI** (async) · **SQLAlchemy** (async) + **Alembic**
- **PostgreSQL** + **pgvector** (HNSW vector search) + **pg_textsearch** (BM25)
- **Amazon S3** (MinIO locally) · **ffmpeg**

**Frontend**
- **React 18** + **Vite** · **TanStack Query** · **TailwindCSS** + **shadcn/ui**
- **react-markdown** + **KaTeX** (math) · **framer-motion** · SSE streaming

---

## 12. Suggested visuals checklist

Aim for **3–4 strong diagrams + 2 screenshots** (matches the good examples):

- [ ] **Routed cascade** flowchart (`rag-and-ai.md §1`) — the hero visual
- [ ] **Hybrid retrieval** flowchart (BM25 + vector → RRF → neighbors, `§4`)
- [ ] **System-at-a-glance** write/read + Postgres (`docs/README.md`)
- [ ] **Video pipeline** strip or status state-machine (`video-processing.md`)
- [ ] **Screenshot:** VideoPlayer with the chat open + a **citation pill / popover** deep-linking a timestamp
- [ ] **Screenshot:** CourseChat answering with sources
- [ ] Optional: the 10-table **ER diagram** (`data.md`) if there's room

> Mermaid diagrams in `docs/` can be exported to clean SVG/PNG (e.g. mermaid.live) for the poster.

---

## 13. What to de-emphasize / honest framing

Keep the poster confident but don't over-claim (graders notice):
- **Video-only** in this version — no PDF/slides/notes ingestion yet (it's on the roadmap; schema is
  already a *unified* corpus that could hold non-video chunks).
- **Chapters are wired but dormant** (`CHAPTERS_ENABLED=false`) — the retrieval path is
  chapter-aware, but semantic chapter titles are off by default. Don't show fake chapter names.
- **Processing runs in-process** (FastAPI `BackgroundTasks`), not a real worker/queue — fine for a
  demo, explicitly a "next step" for scale.
- **No app-level rate limiting**, and model "thinking" is live-only (not persisted).

These are legitimate scoping decisions — present them as roadmap, not as gaps.

---

### Appendix: where each claim was verified

| Claim | File |
|---|---|
| Router actions + default-to-retrieve; DSPy signatures & modules; `streamify` | `app/ai/teaching_assistant.py` |
| 5-path retrieval cascade | `app/rag/course_retriever.py` |
| BM25 + vector + RRF (`k0=60`, top 8), neighbor ±1 (max 6), HNSW | `app/rag/hybrid_retrieve.py` |
| `EMBEDDING_DIMS = 1536` | `app/rag/embedding_config.py` |
| Model tier map (haiku/sonnet/flash per role) | `app/ai/model_roles.py` |
| Model default strings + provider fallback | `app/core/settings.py` |
| Dependency stack | `backend/pyproject.toml`, `frontend/package.json` |
| Narrative / diagrams | `docs/*.md` (cross-checked against code above) |
