"""Narration + music mix with sidechain ducking — reference pipeline logic."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

from .config import (
    MUSIC_BED_DB,
    MUSIC_BED_GAIN_MAX_DB,
    MUSIC_BED_GAIN_MIN_DB,
    MUSIC_DUCK_ATTACK,
    MUSIC_DUCK_RATIO,
    MUSIC_DUCK_RELEASE,
    MUSIC_DUCK_THRESHOLD,
    MUSIC_VOLUME,
    MUSIC_VOLUME_MAX,
    MUSIC_VOLUME_MIN,
    VOICE_GAIN_MAX_DB,
    VOICE_GAIN_MIN_DB,
    VOICE_TARGET_DB,
)
from .utils import run_cmd

# Narration conditioning, shared by the single-video and storyboard mixes so
# both render paths land on identical sound. Level is handled separately, by
# the measured static gain in mix_filter_graph.
VOICE_FILTER = (
    "highpass=f=70,lowpass=f=14000,acompressor="
    "threshold=0.08:ratio=2.5:attack=15:release=180"
)

# Peak ceiling for the finished mix.
#
# This is deliberately a limiter, not a `loudnorm`. A final
# `loudnorm=I=-16:LRA=7` force-compressed the ~21 dB voice-over-music range
# the mix is built around, lifting narration ~5 dB while pulling the music bed
# ~5 dB *down* whenever narration paused -- i.e. the music audibly dropped out
# during silence. A limiter caps peaks without touching the balance.
MIX_LIMITER = "alimiter=limit=0.97:level=disabled"


def clamp_music_volume(value) -> float:
    """music_volume as a 0.0-1.0 multiplier, falling back on junk values."""
    try:
        volume = float(value)
    except (TypeError, ValueError):
        return MUSIC_VOLUME
    if volume != volume:  # NaN
        return MUSIC_VOLUME
    return max(MUSIC_VOLUME_MIN, min(MUSIC_VOLUME_MAX, volume))


def measure_lufs(path: str):
    """Integrated loudness of an audio file in LUFS, or None if unmeasurable.

    This is an analysis-only pass: `loudnorm` is used to *report* loudness but
    never to *process* the signal. Analysis is reproducible, so the corrective
    gain derived from it is stable across renders -- unlike a `loudnorm` filter
    in the graph, which is not.
    """
    try:
        probe = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-nostats", "-i", path,
                "-af", f"loudnorm=I={MUSIC_BED_DB}:print_format=json",
                "-f", "null", "-",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", probe.stderr, re.S)
    if not match:
        return None
    try:
        return float(json.loads(match.group(0))["input_i"])
    except (KeyError, TypeError, ValueError):
        return None


def _corrective_gain(path, target_db, min_db, max_db) -> float:
    """Linear multiplier that moves a file's integrated loudness to target_db."""
    if not path or not os.path.exists(path):
        return 1.0
    measured = measure_lufs(path)
    if measured is None:
        return 1.0
    gain_db = max(min_db, min(max_db, target_db - measured))
    return 10.0 ** (gain_db / 20.0)


def music_bed_volume(music_path, volume) -> float:
    """Bed multiplier that lands any track at MUSIC_BED_DB, then scales it.

    A single static `volume` replaces the old per-render `loudnorm` on the bed,
    so a quiet built-in track and a loud user upload sit at the same level
    without the bed's level moving between runs.
    """
    return clamp_music_volume(volume) * _corrective_gain(
        music_path, MUSIC_BED_DB, MUSIC_BED_GAIN_MIN_DB, MUSIC_BED_GAIN_MAX_DB
    )


def voice_volume(voice_path) -> float:
    """Multiplier that lands narration at VOICE_TARGET_DB."""
    return _corrective_gain(
        voice_path, VOICE_TARGET_DB, VOICE_GAIN_MIN_DB, VOICE_GAIN_MAX_DB
    )


def mix_filter_graph(
    voice_chain: str, music_src: str, volume, music_path=None, voice_path=None
) -> str:
    """Build the narration+music filter graph, output labelled [out].

    Every level in this graph is a *static* gain derived from a measurement
    pass, and the mix ends in a peak limiter. That is deliberate: a `loudnorm`
    filter inside a `filter_complex` graph is not reproducible run-to-run, and
    the drift it caused moved the music bed by up to 20 dB between identical
    renders -- which is what made background music appear to drop out during
    narration. Only `loudnorm`'s analysis output is used, never its processing.

    `voice_chain` is the filter string applied to the voice input, `music_src`
    is the input label holding the music, and the two paths are used only to
    measure the gains.
    """
    return (
        f"[0:a]{voice_chain},volume={voice_volume(voice_path):.5f}[voice];"
        f"[{music_src}]volume={music_bed_volume(music_path, volume):.5f}[music];"
        f"[music][voice]sidechaincompress="
        f"threshold={MUSIC_DUCK_THRESHOLD}:ratio={MUSIC_DUCK_RATIO}:"
        f"attack={MUSIC_DUCK_ATTACK}:release={MUSIC_DUCK_RELEASE}[ducked];"
        f"[voice][ducked]amix=inputs=2:duration=first:"
        f"dropout_transition=2:normalize=0,{MIX_LIMITER}[out]"
    )


def create_final_audio(narration, music_path, hook, hook_duration, settings):
    """Mix narration (delayed by hook) with ducked background music."""
    final_audio = os.path.join(settings.temp_dir, "final_audio.wav")

    delay_ms = int(float(hook_duration) * 1000) if hook else 0
    voice_delay = f"adelay={delay_ms}|{delay_ms}," if hook else ""

    if music_path and os.path.exists(music_path):
        # Keep the real extension — ffmpeg is handed a path whose suffix
        # matches its container.
        ext = os.path.splitext(music_path)[1].lower() or ".mp3"
        music_runtime = os.path.join(settings.temp_dir, f"music{ext}")
        shutil.copy2(music_path, music_runtime)

        run_cmd(
            [
                "ffmpeg", "-y",
                "-i", narration,
                "-stream_loop", "-1",
                "-i", music_runtime,
                "-filter_complex", mix_filter_graph(
                    voice_delay + VOICE_FILTER, "1:a", settings.music_volume,
                    music_path=music_runtime, voice_path=narration,
                ),
                "-map", "[out]",
                "-ar", "48000",
                "-ac", "2",
                "-c:a", "pcm_s16le",
                final_audio,
            ]
        )
    else:
        if music_path:
            print(f"[audio] music track missing, rendering narration only: {music_path}")
        # Same voice conditioning and ceiling as the music path, so a
        # music-free render is not louder or duller than a music-backed one.
        run_cmd(
            [
                "ffmpeg", "-y",
                "-i", narration,
                "-af",
                f"{voice_delay}{VOICE_FILTER},"
                f"volume={voice_volume(narration):.5f},{MIX_LIMITER}",
                "-ar", "48000",
                "-ac", "2",
                "-c:a", "pcm_s16le",
                final_audio,
            ]
        )

    return final_audio
