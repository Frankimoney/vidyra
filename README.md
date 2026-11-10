# Vidyra

**Turn sports stories into videos.**

Vidyra is a mobile-first AI sports-video generator. A user pastes a script,
adds images or one video, picks narration/captions/style, and Vidyra renders a
polished **9:16 MP4** ready for TikTok, Instagram Reels, YouTube Shorts, and
social/sports pages.

```
Script → Voice → Visuals → Captions → Music → Video → Publish
```

---

## Architecture at a glance

```
User (phone browser)
  └─ Vidyra web app          Firebase Hosting (free) + Auth + Firestore
        │  REST API (Bearer Firebase ID token) + media uploads
        ▼
  FastAPI backend            Render free (thin API: auth, uploads, job submit)
        │  render pipeline   RunPod Serverless: Piper → Whisper → FFmpeg
        ▼
  Finished 9:16 MP4          Cloudflare R2, served via presigned URLs
```

- **Frontend:** Vite + React + TypeScript, mobile-first dark UI.
- **Backend:** FastAPI on Render's free tier. It handles auth, Firestore,
  uploads, and job submission; long renders run on **RunPod Serverless** and
  write progress/results back to Firestore directly.
- **Media store:** Firebase Cloud Storage stopped being free (Blaze required
  since **Feb 3, 2026**), so media + finished videos live in **Cloudflare R2**
  and play through short-lived presigned URLs. The API keeps only ephemeral
  work files on disk; nothing is persisted locally.
- **Video engine:** the working logic from the original Colab
  `Sc_Gamecocks_Video_Pipeline` (`Sc_stories.ipynb`), extracted into the
  `pipeline/` package **without rewriting the processing logic**.
- **Gradio dev app** is preserved under `gradio_app/` for quick testing.

**No Firebase Cloud Functions, no Blaze plan.** Firebase is used only for Auth,
Firestore, and static hosting — all covered by the free Spark plan. Compute
runs on Render (free) + RunPod (pay per render); media lives in Cloudflare R2.

---

## Repository layout

```
pipeline/        Reference video engine (Piper, Whisper, FFmpeg) — reused by backend
backend/         FastAPI app: auth, jobs API, catalog, background worker
frontend/        Vite + React + TS mobile-first web app
gradio_app/      Preserved Gradio dev/testing interface (thin wrapper over pipeline/)
config/          voices catalog, deployment config
docs/            ARCHITECTURE.md, DEPLOYMENT.md
firestore.rules  Firestore security rules
storage.rules    Firebase Storage rules (unused by default; needs Blaze)
```

## Getting started (local dev)

### 1. Video engine (Python 3.10+)

```bash
pip install -r backend/requirements.txt
sudo apt-get install -y ffmpeg fonts-dejavu   # Linux; also need piper CLI on PATH
```

FFmpeg must be on `PATH`. `ffprobe` is optional — when it is missing Vidyra
reads durations and dimensions out of ffmpeg's own report instead, so trimmed
FFmpeg builds still work.

On Windows the quickest way to get a working `ffmpeg` without a system-wide
install is the bundled binary:

```bash
pip install imageio-ffmpeg
python -c "import imageio_ffmpeg,os,shutil;p=os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe());shutil.copy(imageio_ffmpeg.get_ffmpeg_exe(), os.path.join(p,'ffmpeg.exe'))"
# then add that binaries\ directory to PATH
```

Wire up Engine + Gradio:

```bash
mkdir -p models/piper config/music storage/outputs
# put .onnx (+ .onnx.json) voices in models/piper, music in config/music

VIDYRA_VOICE_DIR=models/piper \
VIDYRA_MUSIC_DIR=config/music \
VIDYRA_OUTPUT_DIR=storage/outputs \
python -m gradio_app.app
```

Open http://localhost:7860 — this is the reference pipeline behind a test UI.

### 2. Backend (FastAPI)

```bash
cp backend/.env.example backend/.env   # fill Firebase values when ready
uvicorn backend.app.main:app --reload --port 8000
```

- Health: http://localhost:8000/api/health
- Swagger: http://localhost:8000/docs
- In `APP_ENV=development` with `DEV_API_KEY` set, you can call the API with
  `Authorization: Bearer <DEV_API_KEY>` before Firebase is configured.

### 3. Frontend (Vite)

```bash
cd frontend
cp .env.example .env    # fill Firebase + VITE_API_URL=http://localhost:8000
npm install
npm run dev             # http://localhost:5173
```

---

## Testing

- **Engine only:** run the Gradio app and generate a short (1–2 sentence) video.
  Expected: 9:16 MP4 with narration, animated media, hook, captions, music.
- **Backend:** `uvicorn app.main:app --reload --app-dir backend`, then
  `curl -H "Authorization: Bearer <DEV_API_KEY>" localhost:8000/api/health`.
  Create a job with `POST /api/jobs`, poll `GET /api/jobs/{job_id}`.
- **Frontend:** `npm run build` must pass TypeScript + Vite build.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the Render + R2 + RunPod
deployment walkthrough.