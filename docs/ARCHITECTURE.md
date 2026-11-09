# Vidyra Architecture

## Overview

Vidyra is split into four components. The video-processing pipeline is the
*reference implementation* extracted from the working Colab project
(`Sc_stories.ipynb`); it was **not rewritten**, only parameterized (job-scoped
temp dirs, configurable voice/font/music paths, progress callback).

```
Browser (Vite SPA, Firebase Hosting)
  ├─ Firebase Auth        (sign-in, ID tokens)
  ├─ Firestore            (realtime job/status documents)
  └─ FastAPI backend      (REST API, Bearer token)
        ├─ POST /api/uploads → media stored on server disk
        │     (media/{uid}/{uploadId}/{filename})
        ├─ GET /api/media/…  → signed, expiring media URLs
        └─ Background worker (asyncio + threads)
              → pipeline/engine (Piper, Whisper, FFmpeg)
              → publish final MP4 to server disk, sign its URL
              → Firestore status update
```

> **Why no Firebase Storage?** Cloud Storage for Firebase dropped its no-cost
> tier: since **Feb 3, 2026** the Spark plan can no longer access buckets, and
> provisioning requires the pay-as-you-go **Blaze** plan. Vidyra stays free by
> using the server's own disk (`STORAGE_BACKEND=local`, the default) as the
> media store; finished-video URLs are HMAC-signed, short-lived, and
> owner-scoped. A `firebase` driver is preserved behind the same interface for
> anyone who later opts into Blaze (see `backend/app/services/storage/`).

## Data flow per job

1. User uploads media to `POST /api/uploads`; the backend stores each file as
   `media/{uid}/{uploadId}/{filename}` on the server disk and returns its
   storage path. No bytes pass through a third-party store; optional HTTP
   `Range` support lets browsers seek in media.
2. App calls `POST /api/jobs` with script + storage paths + settings. The
   backend rejects any path that is not under `media/{uid}/` (owner check).
3. Backend validates (auth, script length, media count), creates a Firestore
   `videos/{jobId}` doc (`queued`), and enqueues the job on the worker.
4. Worker resolves media (local disk → direct path; firebase → download) and
   runs the engine, reporting progress back to Firestore continuously.
5. Final MP4 is published to `videos/{uid}/{jobId}/final.mp4` on disk; a signed
   URL goes into the job doc's `output.url`, and the doc is marked `completed`
   with `{url, duration, fileSize}`.
6. Temp dir is deleted. The web app's Firestore realtime listener shows
   progress live and renders the finished video; expired links are refreshed
   via `GET /api/jobs/{id}/media-url`.

Job states: `queued → processing → completed | failed`. Progress/stage is
reported honestly by the engine (coarse, stage-bounded percentages — never fake).

## API surface

| Method | Path                  | Auth | Purpose |
|--------|-----------------------|------|---------|
| GET    | `/api/health`         | no   | Liveness + Firebase/storage readiness |
| POST   | `/api/uploads`        | yes  | Multipart media upload → storage path |
| GET    | `/api/media/{path}`   | sig  | Signed, range-capable media/result URL |
| GET    | `/api/catalog/voices` | yes  | Available Piper voices |
| GET    | `/api/catalog/music`  | yes  | Background music tracks |
| GET    | `/api/catalog/presets`| yes  | Pacing/hook/caption/transition options |
| POST   | `/api/jobs`           | yes  | Create + enqueue a job |
| GET    | `/api/jobs`           | yes  | Current user's jobs |
| GET    | `/api/jobs/{id}`      | yes  | Job detail (ownership enforced) |
| GET    | `/api/jobs/{id}/media-url` | yes | Fresh signed playback URL for output |

Auth: FastAPI verifies Firebase ID tokens (`Authorization: Bearer <idToken>`)
with the Firebase Admin SDK. Dev builds may use a static `DEV_API_KEY`.

## Video engine (pipeline/)

| Module        | Responsibility                                   | Reference source |
|---------------|--------------------------------------------------|------------------|
| `narration.py`| split script → Piper per sentence → pauses → polish | Colab Cell 6 |
| `visuals.py`  | zoompan image clips; 9:16 crop of a video        | Colab Cells 9–10 |
| `transitions.py`| xfade stitching of image sequence               | Colab Cell 11 |
| `hook.py`     | hook text builder + black title card via drawtext| Colab Cells 12–13 |
| `audio.py`    | narration + ducked music mix (sidechaincompress) | Colab Cells 14–15 |
| `captions.py` | Whisper word timestamps → karaoke ASS captions   | Colab Cells 16–17 |
| `render.py`   | concat hook+visual, burn captions, final MP4     | Colab Cells 18–19 |
| `engine.py`   | orchestration + progress reporting               | Colab Cell 21 `generate_video` |

Only the code in the Gradio cell (Cell 21) was extracted as the canonical
implementation; the earlier stepwise cells are the same logic in linear form.

## CPU-friendly ARM64 posture

- Piper TTS: `piper-tts` wheels available for ARM64 Linux (ONNX Runtime).
- Whisper (`openai-whisper`, `base` model): CPU-friendly, ~1GB RAM, works on ARM64.
- FFmpeg + DejaVu fonts: standard `apt` packages on Oracle Always Free ARM64.
- No text-to-video models (no Wan / CogVideoX / SVD). Motion comes from FFmpeg
  zoompan + xfade — lightweight and deterministic.

## Security

- Media is stored under `media/{uid}/` on the server disk; job creation rejects
  any path outside the caller's prefix, and served files require an HMAC-signed
  URL (`SIGNED_URL_SECRET`) that expires — no user can guess or reuse another
  user's link.
- Firestore rules restrict reads/writes on `videos/**` to the owner.
- Backend validates token → uid, job ownership on every read.
- Settings whitelisted on the API; script-length and media-count caps.
- Rate limiting (in-memory, per-user) on job creation and uploads.
- Uploads are streamed with a per-file size cap; job temp dirs are removed
  after each job.

## Known operational notes

- Firestore realtime listeners count reads against the Spark free quota
  (50k/day) — fine at current scale.
- Jobs process sequentially by default (`MAX_CONCURRENCY=1`); raise only if the
  Oracle instance has headroom.
- Whisper model is loaded lazily once and cached in the worker process.