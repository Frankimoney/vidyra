"""Download the Piper voice models a render pod needs.

Reads config/voices.json (the same catalog the API serves) and writes each
.onnx + .onnx.json pair into models/piper, which is VIDYRA_VOICE_DIR. Run at
image build time so a cold render does not wait on Hugging Face.

    APP_ROOT=/app python deploy/runpod/fetch_models.py
"""

from __future__ import annotations

import json
import os
import urllib.request

ROOT = os.environ.get("APP_ROOT") or os.getcwd()
CATALOG = os.path.join(ROOT, "config", "voices.json")
DEST = os.path.join(ROOT, "models", "piper")


def _download(url: str, target: str) -> None:
    if os.path.exists(target) and os.path.getsize(target) > 0:
        print(f"skip {os.path.basename(target)} (already present)")
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".part"
    print(f"fetch {url}")
    with urllib.request.urlopen(url, timeout=300) as response:
        with open(tmp, "wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    os.replace(tmp, target)


def main() -> None:
    with open(CATALOG, "r", encoding="utf-8") as handle:
        catalog = json.load(handle)

    for voice in catalog.get("voices") or []:
        voice_id = str(voice.get("id") or "").strip()
        if not voice_id:
            continue
        base = os.path.join(DEST, voice_id)
        if voice.get("url"):
            _download(voice["url"], base + ".onnx")
        if voice.get("json_url"):
            _download(voice["json_url"], base + ".onnx.json")


if __name__ == "__main__":
    main()
