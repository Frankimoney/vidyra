"""Piper voice discovery helper."""

from __future__ import annotations

import os


def find_voices(voice_dir: str) -> dict:
    """Return {display_name: absolute_onnx_path} for every .onnx in a folder."""
    voices = {}
    if not voice_dir or not os.path.isdir(voice_dir):
        return voices
    for filename in sorted(os.listdir(voice_dir)):
        if filename.endswith(".onnx"):
            name = filename[:-5]
            voices[name] = os.path.join(voice_dir, filename)
    return voices


def default_voice_name(voice_dir: str) -> str:
    voices = find_voices(voice_dir)
    for preferred in ("en_US-lessac-medium", "en_US-ryan-medium"):
        if preferred in voices:
            return preferred
    if voices:
        return sorted(voices.keys())[0]
    return ""


def find_music_tracks(music_dir: str) -> dict:
    """Return {track_name: absolute_path} for music files in a folder."""
    from .config import AUDIO_EXTENSIONS

    tracks = {}
    if not music_dir or not os.path.isdir(music_dir):
        return tracks
    for filename in sorted(os.listdir(music_dir)):
        if filename.lower().endswith(AUDIO_EXTENSIONS):
            base, _ext = os.path.splitext(filename)
            tracks[base] = os.path.join(music_dir, filename)
    return tracks