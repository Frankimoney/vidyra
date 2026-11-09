# Vidyra — Deployment (Free Tier)

Target: **Oracle Cloud Always Free (ARM64) + Firebase Spark.** No Cloud
Functions, no Blaze plan, no Firebase Storage — Firebase is only Auth,
Firestore, and static Hosting. Media and finished videos live on the Oracle
VM's own disk and are served through expiring HMAC-signed URLs.

```
Browser (Firebase Hosting SPA)  ←→  Oracle VM (FastAPI backend + engine + media store)
                                        └─ Firebase (Auth / Firestore, metadata + realtime)
```

---

## 1. Firebase project (Spark free plan)

> **Note:** Firebase Cloud Storage requires the Blaze (paid) plan since
> **Feb 3, 2026**, so Vidyra does **not** use it. Skip the Storage step below.

1. Create a project in the Firebase console.
2. **Authentication** → Sign-in method → enable **Email/Password** and **Google**.
3. **Firestore** → Create database (production mode) → deploy `firestore.rules`.
4. **Hosting** → add a site (we deploy the `frontend/dist` build).
5. **Create a composite index**: Firestore → Indexes → Composite →
   collection `videos`, fields `userId` (Asc) + `createdAt` (Desc). Needed by
   the "My Videos" realtime query.
6. **Project settings → Service accounts** → Generate new private key.
   Save the JSON as `config/vidyra-firebase-admin.json` (gitignored).
7. **Project settings → Your apps → Web** → register an app; copy the config
   into `frontend/.env`.

## 2. Oracle Cloud Always Free (ARM64 Ubuntu)

Provision an AMD/ARM A1 instance (configurable, 1–4 OCPUs, up to 24 GB RAM —
a 2-OCPU / 12 GB shape suits Vidyra). Open ports **80/443** and **22**.

SSH in and run:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip ffmpeg fonts-dejavu-core uvicorn nginx
python3 -m venv /opt/vidyra/venv
```

### Repo + engine assets

```bash
sudo mkdir -p /opt/vidyra && sudo chown $USER /opt/vidyra
git clone <your-repo> /opt/vidyra/vidyra        # or rsync the project
cd /opt/vidyra/vidyra

# Piper voices → models/piper (see config/voices.json for URLs), e.g.:
mkdir -p models/piper config/music storage/work storage/data
# … place en_US-lessac-medium.onnx(.json) etc. in models/piper,
#     and .mp3 tracks in config/music.

# Python deps
/opt/vidyra/venv/bin/pip install -r backend/requirements.txt
```

### Env

```bash
cp backend/.env.example /opt/vidyra/vidyra/backend/.env
# Firebase: FIREBASE_PROJECT_ID,
#           SERVICE_ACCOUNT_PATH=/opt/vidyra/vidyra/config/vidyra-firebase-admin.json
# APP_ENV=production, CORS_ORIGINS=https://<your-hosting-site>.web.app
# PUBLIC_URL=https://<oracle-host-or-domain>      # used to build signed media URLs
# SIGNED_URL_SECRET=<long random string>          # REQUIRED in production
# STORAGE_BACKEND=local (default)                 # media on this VM's disk
```

### systemd service

`/etc/systemd/system/vidyra.service`:

```ini
[Unit]
Description=Vidyra backend
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/vidyra/vidyra/backend
EnvironmentFile=/opt/vidyra/vidyra/backend/.env
ExecStart=/opt/vidyra/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now vidyra
curl localhost:8000/api/health        # → {"status":"ok","firebase":true}
```

### nginx reverse proxy

`/etc/nginx/sites-available/vidyra` (HTTP-only note: use a free TLS setup such
as Let's Encrypt for production):

```nginx
server {
  listen 80;
  server_name _;

  location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    client_max_body_size 60m;
  }
}
```

## 3. Frontend (Firebase Hosting, free)

```bash
cd frontend
cp .env.example .env      # fill Firebase keys; VITE_API_URL=https://<oracle-host>
npm install
npm run build             # produces dist/

# init Firebase Hosting in frontend/ (answer web-framework/static questions)
# create frontend/firebase.json + .firebaserc as prompted
npx firebase deploy --only hosting
```

Result: the Vidyra app is live at `https://<site>.web.app`. Phones can create
videos, watch progress in realtime, and download/share the finished MP4.

## 4. Smoke test checklist

1. `GET /api/health` → `{"status":"ok","firebase":true,"storage":"local"}` (both on VM and via HTTPS).
2. Sign up with email; create a short (1–2 sentence) video with 2 images.
3. Watch the job go `queued → processing → completed` with live stage/%.
4. Play, download, and share the MP4 from the phone (signed `/api/media/…` URLs).
5. Confirm temp dirs are cleaned and media exists:
   `ls storage/work` is empty after completion, `ls storage/data/videos` has the published MP4s.

## Tuning notes

- `MAX_CONCURRENCY=1` = one job at a time (default). Increase only with RAM headroom.
- Whisper `base` is cached in-process; each backend instance loads it once (~1GB RAM).
- Free-tier quotas to watch: Firestore 50k reads / 20k writes per day; Oracle disk
  for stored media+results (Always Free includes ~200 GB block storage). Add an
  Object Storage driver later if you outgrow the VM disk or want backups.