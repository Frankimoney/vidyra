"""Public (no-auth) endpoints for shared video watch pages and link previews."""

from __future__ import annotations

import html

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..services import jobs as jobs_service
from ..services.shares import parse_share_token
from ..services.storage.local import as_absolute

router = APIRouter()


def _resolve(request: Request, token: str) -> tuple[dict | None, dict]:
    settings = request.app.state.settings
    job_id = parse_share_token(token, settings.sign_secret)
    if not job_id:
        return None, {}
    job = jobs_service.get_job(job_id)
    if not job:
        return None, {}
    output = job.get("output") or {}
    storage_path = output.get("storagePath")
    if not storage_path:
        return None, {}
    payload = {
        "jobId": job_id,
        "title": job.get("title", "Untitled video"),
        "url": as_absolute(
            request.app.state.storage.share_url(storage_path),
            settings.public_url,
        ),
        "poster": None,
        "pageName": (job.get("niche") or "").strip(),
        "duration": output.get("duration"),
        "createdAt": job.get("createdAt", ""),
    }
    poster_path = output.get("posterStoragePath")
    if poster_path:
        payload["poster"] = as_absolute(
            request.app.state.storage.share_url(poster_path),
            settings.public_url,
        )
    return job, payload


@router.get("/public/video/{token}")
async def public_video(token: str, request: Request):
    _, payload = _resolve(request, token)
    if not payload:
        raise HTTPException(status_code=404, detail="This video is no longer available.")
    return payload


@router.get("/public/share/{token}", response_class=HTMLResponse)
async def public_share_html(token: str, request: Request):
    """Crawler/browser-friendly page with Open Graph meta, redirecting to the SPA."""
    _, payload = _resolve(request, token)
    settings = request.app.state.settings

    if not payload:
        return HTMLResponse(
            "<html><body><h1>This video is no longer available.</h1></body></html>",
            status_code=404,
        )

    frontend = settings.frontend_url.rstrip("/")
    watch_url = f"{frontend}/v/{token}"
    title = html.escape(payload["title"] or "Vidyra video")
    page_name = html.escape(payload["pageName"] or "")
    desc = (
        f"Watch @{page_name} on Vidyra"
        if page_name
        else "Watch this video made with Vidyra."
    )
    poster_attr = html.escape(payload["poster"] or "") if payload.get("poster") else ""
    url_attr = html.escape(payload["url"] or "")
    watch_attr = html.escape(watch_url)
    poster_tag = (
        f'<meta property="og:image" content="{poster_attr}" />'
        if poster_attr
        else ""
    )
    page_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <meta name="description" content="{desc}" />
  <meta property="og:type" content="video.other" />
  <meta property="og:title" content="{title}" />
  <meta property="og:description" content="{desc}" />
  <meta property="og:video" content="{url_attr}" />
  <meta property="og:video:type" content="video/mp4" />
  {poster_tag}
  <meta name="twitter:card" content="player" />
  <meta name="twitter:title" content="{title}" />
  <meta name="twitter:image" content="{poster_attr or url_attr}" />
  <meta http-equiv="refresh" content="0; url={watch_attr}" />
</head>
<body>
  <p><a href="{watch_attr}">{title}</a></p>
  <script>window.location.replace('{watch_url}');</script>
</body>
</html>"""
    return HTMLResponse(page_html)