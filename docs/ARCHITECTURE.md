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
  └─ FastAPI backend      (Render free; REST API, Bearer token)
        ├─ POST /api/uploads → media stored in Cloudflare R2
        │     (media/{uid}/{uploadId}/{filename})
        ├─ POST /api/jobs    → submits a run to RunPod Serverless
        └─ RunPod pipeline   (Piper, Whisper, FFmpeg)
              → reads sources from R2, writes progress to Firestore
              → publishes the final MP4 to R2, signs a presigned URL
```

> **Why no Firebase Storage?** Cloud Storage for Firebase dropped its no-cost
> tier: since **Feb 3, 2026** the Spark plan can no longer access buckets, and
> provisioning requires the pay-as-you-go **Blaze** plan. Vidyra uses
> **Cloudflare R2** as its only media store (`STORAGE_BACKEND=r2`); the API
> keeps only ephemeral work files on disk. Media URLs are short-lived
> presigned links, and share links can use an R2 custom domain.

## Data flow per job

1. User uploads media to `POST /api/uploads`; the backend writes each file to
   R2 under `media/{uid}/{uploadId}/{filename}` and returns its storage path.
   The Render instance never keeps the file.
2. App calls `POST /api/jobs` with script + storage paths + settings. The
   backend rejects any path that is not under `media/{uid}/` (owner check).
3. Backend validates (auth, script length, media count), creates a Firestore
   `videos/{jobId}` doc (`queued`), and submits `{job_id}` to the RunPod
   pipeline endpoint.
4. The RunPod worker loads the job, downloads sources from R2 into ephemeral
   scratch, runs the engine, and reports progress to Firestore continuously.
5. The final MP4 is published to `videos/{uid}/{jobId}/final.mp4` in R2; a
   presigned URL goes into the job doc's `output.url`, and the doc is marked
   `completed` with `{url, duration, fileSize}`.
6. The worker deletes its scratch dir. The web app's Firestore realtime
   listener shows progress live and renders the finished video; expired links
   are refreshed via `GET /api/jobs/{id}/media-url`.

The API's `/api/media/{path}` route is a development-only fallback and returns
404 for R2 storage; deployed media is always served by presigned R2 URLs.

Job states: `queued → processing → completed | failed`. Progress/stage is
reported honestly by the engine (coarse, stage-bounded percentages — never fake).

## API surface

| Method | Path                  | Auth | Purpose |
|--------|-----------------------|------|---------|
| GET    | `/api/health`         | no   | Liveness + Firebase/storage readiness |
| POST   | `/api/uploads`        | yes  | Multipart media upload → storage path |
| GET    | `/api/media/{path}`   | sig  | Dev-only local fallback (404 on R2) |
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

## Render worker posture

- The pipeline image (`deploy/runpod/pipeline.Dockerfile`) runs Piper,
  Whisper, and FFmpeg. It has no GPU-specific dependencies; RunPod executes it
  on the endpoint size you choose.
- Whisper (`openai-whisper`, `base` model) is imported lazily and loaded once
  per pod instance.
- No text-to-video models in the pipeline (no Wan / CogVideoX / SVD). Motion
  comes from FFmpeg zoompan + xfade — lightweight and deterministic.
- The Render API image omits Piper/Whisper entirely (`requirements-api.txt`).

## Security

- Media is stored under `media/{uid}/` in R2; job creation rejects any path
  outside the caller's prefix. Playback URLs are short-lived R2 presigns, and
  share URLs are HMAC-signed (`SIGNED_URL_SECRET`) and expire — no user can
  guess or reuse another user's link.
- Firestore rules restrict reads/writes on `videos/**` to the owner.
- Backend validates token → uid, job ownership on every read.
- Settings whitelisted on the API; script-length and media-count caps.
- Rate limiting (in-memory, per-user) on job creation and uploads.
- Uploads are streamed with a per-file size cap; job temp dirs are removed
  after each job.

## Known operational notes

- Firestore realtime listeners count reads against the Spark free quota
  (50k/day) — fine at current scale.
- The API only submits jobs; RunPod's concurrency controls parallel renders.
- Whisper is loaded lazily once and cached per RunPod worker process.
- Render free instances sleep: `BACKGROUND_LOOPS=false` in production and
  `python -m app.cron` runs cleanup and recurring series.
- The API's `/api/media/{path}` route is development-only; R2 media is served
  by presigned URLs.