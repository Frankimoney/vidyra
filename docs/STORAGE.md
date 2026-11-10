# Online storage (Cloudflare R2)

Vidyra stores uploads, Studio outputs, cast portraits and finished videos in an
S3-compatible bucket. Cloudflare R2's free tier (10 GB storage, free egress) is
the recommended option.

The FastAPI backend does the rendering; only **persisted media** lives in R2.

## 1. Create the bucket

1. Cloudflare dashboard → **R2** → **Create bucket**.
2. Give it a name (for example `vidyra-media`). Keep it private.
3. Copy your **Account ID** from the R2 overview page.

## 2. Create an S3 API token

1. R2 → **Manage R2 API Tokens** → **Create API token**.
2. Permission: **Object Read & Write**.
3. Copy the **Access Key ID** and **Secret Access Key** immediately.

## 3. Configure Vidyra

Copy the template:

```powershell
Copy-Item backend\config\storage.env.example backend\config\storage.env
```

Fill it in:

```env
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
S3_REGION=auto
S3_BUCKET=vidyra-media
S3_ACCESS_KEY=<access key id>
S3_SECRET_KEY=<secret access key>
```

`backend/config/storage.env` is gitignored. Never commit it or paste secrets
into chat.

## 4. Allow browser playback (CORS)

In R2 → bucket → **Settings** → **CORS policy**, add:

```json
[
  {
    "AllowedOrigins": [
      "http://localhost:5173",
      "http://localhost:8000",
      "https://vidyra.web.app"
    ],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "ExposeHeaders": ["Content-Range", "Accept-Ranges", "Content-Length"],
    "MaxAgeSeconds": 3600
  }
]
```

Add your custom frontend domain if you use one.

## 5. Migrate existing local files

With `storage.env` in place and `boto3` installed:

```powershell
.venv\Scripts\python.exe scripts\migrate_storage_to_s3.py
```

This uploads every object under `storage/data/{media,studio,videos}` using the
same keys, so existing job documents keep working. Already-uploaded keys are
skipped.

## 6. Restart and verify

```powershell
Start Vidyra PC.bat
curl http://localhost:8000/api/health
```

Expected:

```json
{"status":"ok","firebase":true,"storage":"s3"}
```

Open an existing video and a Studio run to confirm playback and thumbnails.

## Notes

- Presigned URLs are short-lived (24 h for previews, 7 days for shares). The app
  refreshes job playback URLs automatically.
- R2 has no egress fees; the bucket can stay private.
- Runpod network volumes work with the same driver but are billed per GB-month.
- If you switch back to local storage, set `STORAGE_BACKEND=local` or remove
  `storage.env`.
