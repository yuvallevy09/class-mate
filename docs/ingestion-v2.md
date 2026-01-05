# Ingestion v2 (planned): Course files + Video Assets + Transcription

This project **does not use Bunny**. Video ingestion is implemented as **direct uploads to S3/MinIO** plus a server-side transcription pipeline.

## Concepts (names we use)

- **Course content**: a user-created item under a course (notes/exams/resources/etc.), optionally with an attached file (e.g. PDFs).
- **Video asset**: an uploaded video file registered to a course for transcription and retrieval.
- **Transcript segments**: timestamped segments of text for a video asset (start/end seconds + text + language).

## Desired ingestion flows

### 1) Documents / PDFs

1. Frontend requests presigned upload URL: `POST /api/v1/uploads/presign`
2. Browser uploads directly to S3/MinIO (`PUT`)
3. Frontend registers content: `POST /api/v1/courses/{courseId}/contents`
4. Backend optionally indexes PDFs into the per-course Chroma store (RAG).

### 2) Videos (Video Assets)

1. Frontend requests presigned upload URL: `POST /api/v1/uploads/presign`
2. Browser uploads video directly to S3/MinIO (`PUT`)
3. Frontend registers a video asset (new API):
   - `POST /api/v1/courses/{courseId}/video-assets`
4. Backend transcribes and persists transcript segments:
   - `POST /api/v1/video-assets/{videoAssetId}/transcribe`

## Transcription pipeline (server-side)

For each uploaded video asset:

1. Download the video from S3/MinIO.
2. Extract audio with ffmpeg (normalize to mono, 16kHz WAV).
3. Transcribe using Runpod serverless (faster-whisper worker).
4. Run `whisper-timestamped` to produce reliable segment start/end timestamps.
5. Persist the result as rows in `transcript_segments`.
6. (Optional) Index transcript segments into the course Chroma collection for RAG-backed chat citations.

## Proposed API surface (OpenAPI contract)

- **Uploads**
  - `POST /api/v1/uploads/presign`
- **Course contents**
  - `GET /api/v1/courses/{courseId}/contents?category=...`
  - `POST /api/v1/courses/{courseId}/contents`
  - `DELETE /api/v1/contents/{contentId}`
  - `GET /api/v1/contents/{contentId}/download`
- **Video assets (new)**
  - `GET /api/v1/courses/{courseId}/video-assets`
  - `POST /api/v1/courses/{courseId}/video-assets`
  - `GET /api/v1/video-assets/{videoAssetId}`
  - `POST /api/v1/video-assets/{videoAssetId}/transcribe`
  - `GET /api/v1/video-assets/{videoAssetId}/segments?language_code=...`


