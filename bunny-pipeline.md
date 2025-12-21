# ClassMate — Bunny Video RAG MVP Plan 

## What Bunny features we rely on (MVP scope)

We will use Bunny Stream for hosting + processing, and Bunny’s AI outputs as retrieval metadata:

1. **Stream Webhooks** to drive ingestion:

* `3 = Finished` (video is playable)
* `9 = CaptionsGenerated` (automatic captions ready)
* `10 = TitleOrDescriptionGenerated` (AI title/description ready) ([bunny.net Developer Hub][1])

2. **Caption (WebVTT) file URL pattern** (stable path on your pull zone):
   `https://{pull_zone_url}.b-cdn.net/{video_id}/captions/{language_code}.vtt` ([bunny.net Developer Hub][2])

3. **Smart Chapters** (optional but strongly recommended for “works well” UX): it generates chapters from the caption track and can be enabled at the library level or generated per-video. ([bunny.net Developer Hub][3])

4. **Jump-to-timestamp playback**

* Embed param `t=` supported by Bunny embed URLs (multiple formats). ([bunny.net Developer Hub][4])
* Or use **player.js** and call `setCurrentTime(seconds)` on the iframe player. ([bunny.net Developer Hub][5])

5. **Security (recommended)**

* **Embed view token auth**: sign the iframe URL to prevent unauthorized embedding. ([bunny.net Developer Hub][6])
* **CDN token auth**: sign pull-zone URLs for direct file access (HLS/MP4/VTT) if needed. ([bunny.net Developer Hub][7])

---

## 1) Canonical metadata contract (do this first)

Define and use these keys consistently in **DB rows, Chroma metadata, and chat citations**:

**IDs / routing**

* `course_id`
* `video_asset_id`
* `video_guid` (Bunny video ID / guid)
* `video_library_id`
* `pull_zone_url`

**RAG typing**

* `doc_type ∈ {"lecture","chapter","segment","pdf"}`

**Time & language**

* `start_sec`, `end_sec`
* `language_code` (e.g., `"en"`, `"he"`)

**Chapter metadata**

* `chapter_id`
* `chapter_title`

**Definition of done**

* One shared schema documented + implemented in ingestion + retrieval + citations.

---

## 2) Data model (Postgres is the source of truth)

You already store transcript segments; extend slightly:

### 2A) `video_assets` (or equivalent)

Fields you need for ingestion + playback:

* `provider="bunny"`, `video_library_id`, `video_guid`, `pull_zone_url`
* `title`, `description` (updated when webhook `10` arrives) ([bunny.net Developer Hub][1])
* `status` (queued/encoding/finished/captions_ready/failed)
* `captions_language_code` (default), `captions_vtt_url` (optional stored)
* `transcript_ingested_at`, `chapters_ingested_at` (optional)

### 2B) `video_chapters` (recommended)

Store chapters as queryable rows (not JSON) so you can filter/expand context cleanly:

* `id`, `video_asset_id`, `start_sec`, `end_sec`, `title`, `source`

### 2C) `transcript_segments` (you have this)

Add:

* `chapter_id` (nullable FK)
* `chapter_title` (nullable denormalized convenience)

**Why Postgres?**

* Debuggable ground truth, deterministic reprocessing, and fast “neighbor segment” fetch for context expansion.

---

## 3) Ingestion pipeline (webhooks → VTT/chapters → DB → Chroma)

### 3A) Upload flow (keep big files off FastAPI)

* Frontend uploads directly to Bunny (engineer can choose HTTP upload method; Bunny provides an HTTP API guide). ([bunny.net Developer Hub][8])
* Backend creates/stores the `video_assets` record and returns `video_guid` etc.

### 3B) Webhook handler (required): `POST /api/webhooks/bunny/stream`

Handle these statuses idempotently: ([bunny.net Developer Hub][1])

* **3 Finished** → mark playable
* **9 CaptionsGenerated** → enqueue job: “fetch VTT + parse + upsert segments + index”
* **10 TitleOrDescriptionGenerated** → update `video_assets.title/description` and re-upsert the **lecture doc** into Chroma

### 3C) Fetch & parse captions (VTT → cue list → segments)

* Download: `https://{pull_zone_url}.b-cdn.net/{video_id}/captions/{language_code}.vtt` ([bunny.net Developer Hub][2])
* Parse cues → normalize to “embedding-friendly segments”:

  * merge cues until ~300–800 chars OR ~10–30 seconds
  * store: `(start_sec, end_sec, text, language_code)`

### 3D) Chapters ingestion (recommended for MVP “works well”)

If Smart Chapters is enabled, ingest chapters after they exist. Smart Chapters is based on the caption track. ([bunny.net Developer Hub][3])
Implementation options for MVP:

* **Best**: fetch via API if your current Bunny plan/library exposes it cleanly
* **Fallback**: admin/manual import (copy from dashboard/export) until API wiring is ready

### 3E) Assign each transcript segment to a chapter (critical)

After both exist:

* For each segment `(start_sec, end_sec)`, find chapter with max overlap (or nearest start).
* Persist `chapter_id`, `chapter_title` onto the segment row.

**Definition of done**

* After ingestion, segments exist in DB and have chapter metadata when chapters exist.

---

## 4) Chroma index design (single per-course collection, multiple doc types)

Keep **one Chroma collection per course**, but store **coarse-to-fine docs** together:

### 4A) Doc types inserted into Chroma

1. **Lecture doc** (`doc_type="lecture"`)

* `text = "{title}\n{description}"`
* `metadata = {course_id, video_asset_id, video_guid, language_code?, doc_type}`

2. **Chapter doc** (`doc_type="chapter"`)

* `text = chapter_title` (MVP)
* `metadata = {course_id, video_asset_id, chapter_id, start_sec, end_sec, doc_type}`

3. **Segment doc** (`doc_type="segment"`)

* `text = transcript_segment.text`
* `metadata = {course_id, video_asset_id, video_guid, chapter_id, chapter_title, start_sec, end_sec, language_code, doc_type}`

(Keep your existing `pdf` docs too if you want one unified “course knowledge index”.)

### 4B) Stable IDs (prevents duplicates on reindex)

* lecture: `lecture:{video_asset_id}`
* chapter: `chapter:{chapter_id}`
* segment: `segment:{segment_id}` (or include `language_code` if needed)

**Definition of done**

* You can query Chroma with filters like:

  * `doc_type="lecture"` only
  * `doc_type="chapter" AND video_asset_id in [...]`
  * `doc_type="segment" AND chapter_id in [...]`

---

## 5) Retrieval pipeline (layered retrieval + anti-frustration)

Implement retrieval in a dedicated module so ChatEngine stays thin.

### Stage A — Lecture retrieval (coarse routing)

* Query Chroma only over `doc_type="lecture"` with the user question, `k=3..6`
* Gate (see below). If it fails → fallback (“I don’t have enough indexed video content yet” or try PDFs)

### Stage B — Chapter retrieval (narrow within candidate lectures)

* Query `doc_type="chapter"` filtered to the candidate `video_asset_id`s, `k=6..12`
* Gate again

### Stage C — Segment retrieval (ground truth evidence)

* Query `doc_type="segment"` filtered to candidate `chapter_id`s, `k=10..25`
* Gate again, then build context windows

### Context windows (this is what prevents frustration)

1. **Time clustering**

* Sort segment hits by `(video_asset_id, start_sec)`
* Merge hits that are within ~30–60 seconds gaps into a single window

2. **Parent expansion (Postgres neighbor fetch)**
   Once you pick “winning windows”:

* Pull neighboring segments from DB in the same chapter within ±2–5 minutes (cap by max chars/tokens)
* This is what makes “definition + axioms” answers complete even if embeddings hit only one cue.

### Gating (hit/miss decision)

At each stage:

* **Threshold gate**: best distance must be “good enough”
* **Margin gate**: best must beat second-best by a minimum margin

(You already solved “score vs distance” ambiguity for Chroma; keep logging top distances and thresholds so you can tune quickly.)

**Definition of done**

* Questions like “when did the professor define groups and what axioms?” reliably return:

  * the correct lecture,
  * the correct chapter region,
  * expanded transcript context (not a single chopped line),
  * timestamped citations.

---

## 6) Answer format + citations (backend contract)

For each selected context window, return a citation like:

* `type="video"`
* `videoGuid`
* `startSec`, `endSec`
* `chapterTitle` (optional but ideal)
* optionally `languageCode`

Frontend can render citations and support “jump to moment”.

---

## 7) Jump-to-timestamp UX (two supported approaches)

### Option A (fastest): embed `t=...`

Bunny embed URL pattern:
`https://iframe.mediadelivery.net/embed/{video_library_id}/{video_id}` ([bunny.net Developer Hub][4])
Parameter `t` sets start time (multiple formats). ([bunny.net Developer Hub][4])

### Option B (best UX): player.js seek (no iframe reload)

Use Bunny’s playback control API + player.js and call:

* `player.setCurrentTime(seconds)` ([bunny.net Developer Hub][5])

---

## 8) Background jobs (strongly recommended for MVP reliability)

Video ingestion can be slow (VTT fetch, parsing, embedding, upserts). Don’t risk timeouts or restarts.

Minimal worker options:

* **RQ** (simple Redis queue) ([Python RQ][9])
* **SAQ** (async-first job queue on Redis; fits your FastAPI async stack well) ([SAQ Documentation][10])
* **Celery** (heavier but very mature) ([Celery Documentation][11])

If you stay on FastAPI `BackgroundTasks` for dev, that’s fine—but for an MVP users rely on, use a worker.

---

## 9) Security (don’t ship without a plan)

* If you embed Bunny iframes: enable and sign **Embed view token authentication**. ([bunny.net Developer Hub][6])
* If you fetch direct pull-zone URLs (HLS/MP4/VTT): protect with **CDN token authentication**. ([bunny.net Developer Hub][7])
* You can also fetch captions server-side and store them (or derived segments) so clients never access raw VTT.

---

## MVP “Definition of Done” checklist

* [ ] Webhooks handle statuses **3/9/10** idempotently ([bunny.net Developer Hub][1])
* [ ] Captions fetched from the documented VTT URL pattern ([bunny.net Developer Hub][2])
* [ ] Chapters ingested (Smart Chapters) and segments assigned to chapters ([bunny.net Developer Hub][3])
* [ ] Chroma contains **lecture + chapter + segment** docs in the per-course collection
* [ ] Layered retrieval (lecture → chapter → segment) with gating + context expansion
* [ ] Chat returns timestamped citations and frontend can seek (embed `t` or player.js) ([bunny.net Developer Hub][4])

---

## Bunny docs (URLs)

```txt
Stream webhooks (status codes incl. Finished=3, CaptionsGenerated=9, TitleOrDescriptionGenerated=10)
https://docs.bunny.net/docs/stream-webhook

Video storage structure (caption VTT URL pattern)
https://docs.bunny.net/docs/stream-video-storage-structure

Embedding videos (iframe URL pattern + t= parameter)
https://docs.bunny.net/docs/stream-embedding-videos

Playback control API (player.js, setCurrentTime)
https://docs.bunny.net/docs/playback-control-api

Smart chapters
https://docs.bunny.net/docs/stream-smart-chapters

Stream security overview
https://docs.bunny.net/docs/stream-security

Embedded view token authentication
https://docs.bunny.net/docs/stream-embed-token-authentication

CDN token authentication
https://docs.bunny.net/docs/cdn-token-authentication

Stream API overview
https://docs.bunny.net/reference/stream-api-overview

Upload via HTTP API (one possible upload method)
https://docs.bunny.net/docs/stream-uploading-videos-through-our-http-api

Video library “Get Languages”
https://docs.bunny.net/reference/videolibrarypublic_index3
```



[1]: https://docs.bunny.net/docs/stream-webhook "Webhooks"
[2]: https://docs.bunny.net/docs/stream-video-storage-structure "Video storage structure"
[3]: https://docs.bunny.net/docs/stream-smart-chapters "Smart chapters "
[4]: https://docs.bunny.net/docs/stream-embedding-videos "Embedding videos"
[5]: https://docs.bunny.net/docs/playback-control-api "Playback control API"
[6]: https://docs.bunny.net/docs/stream-embed-token-authentication "Embedded view token authentication"
[7]: https://docs.bunny.net/docs/cdn-token-authentication "Token authentication"
[8]: https://docs.bunny.net/docs/stream-uploading-videos-through-our-http-api?utm_source=chatgpt.com "Uploading videos through our HTTP API"
[9]: https://python-rq.org/?utm_source=chatgpt.com "RQ: Simple job queues for Python"
[10]: https://saq-py.readthedocs.io/?utm_source=chatgpt.com "SAQ (Simple Async Queue) documentation - Read the Docs"
[11]: https://docs.celeryq.dev/?utm_source=chatgpt.com "Celery - Distributed Task Queue — Celery 5.6.0 documentation"
