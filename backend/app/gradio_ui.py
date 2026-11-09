"""Standalone Gradio interface for the Vidyra video engine.

Mirrors the reference pipeline's Gradio entrypoint so the engine can be
used without the web app: pick media + script + voice, generate a video.

Run from the repo root:
    python backend/app/gradio_ui.py
then open http://127.0.0.1:7860
"""

from __future__ import annotations

import os
import uuid

import gradio as gr
import whisper

from pipeline.config import RenderSettings
from pipeline.engine import generate_video
from pipeline.voices import find_music_tracks, find_voices

WORK_ROOT = os.path.join("storage", "work", "gradio")
VOICE_DIR = os.environ.get("VIDYRA_VOICE_DIR", "models/piper")
MUSIC_DIR = os.environ.get("VIDYRA_MUSIC_DIR", "config/music")
WHISPER_MODEL = os.environ.get("VIDYRA_WHISPER_MODEL", "base")

_whisper = None


def _get_whisper():
    global _whisper
    if _whisper is None:
        _whisper = whisper.load_model(WHISPER_MODEL)
    return _whisper


def _voices() -> list:
    return sorted(find_voices(VOICE_DIR).keys())


def _tracks() -> list:
    return [""] + sorted(find_music_tracks(MUSIC_DIR).keys())


def generate(
    media,
    script: str,
    voice: str,
    pacing: str,
    speech_rate: float,
    hook: bool,
    captions: bool,
    music: str,
):
    if not script or not script.strip():
        raise gr.Error("Enter a script.")
    if not media:
        raise gr.Error("Upload at least one image or one video.")

    paths = [m.name if hasattr(m, "name") else str(m) for m in media]
    for p in paths:
        if not os.path.isfile(p):
            raise gr.Error(f"Missing media file: {p}")

    voices = find_voices(VOICE_DIR)
    tracks = find_music_tracks(MUSIC_DIR)
    voice_path = voices.get(voice) or (sorted(voices.values()) or [None])[0]
    if not voice_path:
        raise gr.Error("No Piper voice installed on the server.")

    job_dir = os.path.join(WORK_ROOT, uuid.uuid4().hex[:8])
    temp_dir = os.path.join(job_dir, "tmp")
    output_path = os.path.join(job_dir, "final.mp4")
    os.makedirs(temp_dir, exist_ok=True)

    settings = RenderSettings(
        script=script,
        media_files=paths,
        voice_path=voice_path,
        speech_rate=float(speech_rate or 1.0),
        pacing=pacing,
        hook=bool(hook),
        captions=bool(captions),
        music_path=tracks.get(music) if music else None,
        temp_dir=temp_dir,
        output_path=output_path,
    )

    whisper_model = _get_whisper() if captions else None
    generate_video(settings, whisper_model=whisper_model)
    return output_path


demo = gr.Interface(
    fn=generate,
    inputs=[
        gr.File(label="Images (or a single video)", file_count="multiple"),
        gr.Textbox(label="Script", lines=4, placeholder="Describe the moment..."),
        gr.Dropdown(label="Voice", choices=_voices(), value=_voices()[0] if _voices() else None),
        gr.Dropdown(
            label="Pacing",
            choices=["Breaking News", "Normal Sports Story", "High Energy", "Analysis"],
            value="Normal Sports Story",
        ),
        gr.Slider(label="Speech rate", minimum=0.85, maximum=1.15, step=0.05, value=1.0),
        gr.Checkbox(label="Opening hook card", value=True),
        gr.Checkbox(label="Captions", value=True),
        gr.Dropdown(label="Background music", choices=_tracks(), value=""),
    ],
    outputs=gr.Video(label="Your video"),
    title="Vidyra — AI Sports Story Video",
    description="Script + media → 9:16 sports video (Piper narration, captions, music).",
)


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)