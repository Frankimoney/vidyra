"""Multi-scene storyboard rendering.

A storyboard is an ordered list of scenes, each with its own script, voice and
media. Every scene is rendered with the standard single-video pipeline (so it
gets its own narration, captions and transitions), then the scenes are
concatenated into one extended video. The optional hook and background music
are applied once to the final cut rather than per scene.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import replace

from .audio import VOICE_FILTER, mix_filter_graph
from .engine import generate_video
from .hook import build_hook, create_hook
from .narration import clone_lines_for
from .utils import run_cmd, PipelineError


def _concat(files, output, temp_dir):
    """Concatenate same-codec MP4s (all scenes share encoder settings)."""
    list_file = os.path.join(temp_dir, "storyboard_concat.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for path in files:
            f.write(f"file '{os.path.abspath(path)}'\n")
    run_cmd(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            output,
        ]
    )
    return output


def _hook_with_audio(settings):
    """Render the hook title card and give it a silent audio track.

    Scenes always carry an audio stream, so the hook must too for a
    stream-copy concat to stay valid.
    """
    hook_text, hook_subtext = build_hook(
        settings.hook_style, settings.hook_text, settings.hook_subtext
    )
    hook_video = create_hook(
        hook_text, hook_subtext, settings.hook_duration, settings
    )
    hook_av = os.path.join(settings.temp_dir, "hook_av.mp4")
    run_cmd(
        [
            "ffmpeg", "-y",
            "-i", hook_video,
            "-f", "lavfi",
            "-i", "anullsrc=r=48000:cl=stereo",
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            hook_av,
        ]
    )
    return hook_av


def _mix_music(video, music, output, volume):
    """Duck background music under the concatenated narration audio."""
    narration = os.path.splitext(video)[0] + ".wav"
    if not os.path.exists(narration):
        narration = None
    run_cmd(
        [
            "ffmpeg", "-y",
            "-i", video,
            "-stream_loop", "-1",
            "-i", music,
            "-filter_complex", mix_filter_graph(
                VOICE_FILTER, "1:a", volume,
                music_path=music, voice_path=narration,
            ),
            "-map", "0:v",
            "-map", "[out]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-shortest",
            output,
        ]
    )
    return output


def _scene_clone_banks(script, voices, default_voice, banks):
    """Subset of the job's cloned-line banks that this scene speaks."""
    if not banks:
        return {}
    wanted = clone_lines_for(script, voices or {}, default_voice or "")
    out = {}
    for voice_value, texts in wanted.items():
        bank = banks.get(voice_value) or {}
        picked = {text: bank[text] for text in texts if text in bank}
        if picked:
            out[voice_value] = picked
    return out


def generate_storyboard(
    scenes,
    settings,
    whisper_model=None,
    on_progress=None,
    on_stage=None,
):
    """Render every scene and concatenate them into ``settings.output_path``.

    scenes: list of dicts with keys
        script         (str, required)
        voice_path     (str, required)  default voice for the scene
        media_files    (list[str], required) local staged paths
        clip_durations (list, optional)
        transition     (str, optional)
        dialogue_voices(dict, optional) speaker -> voice for "Name:" lines
            (a Piper model path or a "clone:<path>" cloned voice)
        voice_clone_wav(str, optional) pre-cloned narration for this scene
            (used by podcast turns, which voice exactly one line)
    settings: base RenderSettings carrying global options (hook, music,
              captions, dimensions, badge, fonts, ...).
    """
    if not scenes:
        raise PipelineError("Storyboard needs at least one scene.")

    base_temp = os.path.abspath(settings.temp_dir)
    os.makedirs(base_temp, exist_ok=True)
    total = len(scenes)
    scene_files = []

    for index, scene in enumerate(scenes):
        script = (scene.get("script") or "").strip()
        if not script:
            raise PipelineError(f"Scene {index + 1} is missing a script.")
        media = scene.get("media_files") or []
        if not media:
            raise PipelineError(f"Scene {index + 1} needs at least one image or video.")
        voice = scene.get("voice_path") or settings.voice_path
        if not voice:
            raise PipelineError(f"Scene {index + 1} is missing a voice.")

        scene_temp = os.path.join(base_temp, f"scene_{index:03d}")
        os.makedirs(scene_temp, exist_ok=True)
        scene_voices = scene.get("dialogue_voices") or settings.dialogue_voices
        # Hand this scene only the cloned lines it actually speaks, so one
        # pre-cloned bank can serve every scene without lines being consumed
        # by the wrong scene.
        scene_banks = _scene_clone_banks(
            script, scene_voices, voice, settings.clone_line_banks
        )
        scene_settings = replace(
            settings,
            script=script,
            voice_path=voice,
            dialogue_voices=scene_voices,
            clone_line_banks=scene_banks,
            use_clip_audio=bool(scene.get("use_clip_audio", False)),
            # Per-scene pre-cloned track (podcast turns voice one line); the
            # single-video path fills this instead.
            voice_clone_wav=scene.get("voice_clone_wav") or "",
            media_files=list(media),
            clip_durations=list(scene.get("clip_durations") or []),
            trim_starts=list(scene.get("trim_starts") or []),
            clip_focus=list(scene.get("clip_focus") or []),
            clip_zoom=list(scene.get("clip_zoom") or []),
            clip_speeds=list(scene.get("clip_speeds") or []),
            clip_reverse=list(scene.get("clip_reverse") or []),
            clip_freeze=list(scene.get("clip_freeze") or []),
            clip_motions=list(scene.get("clip_motions") or []),
            clip_keyframes=list(scene.get("clip_keyframes") or []),
            target_duration=float(scene.get("duration") or 0.0),
            transition=scene.get("transition") or settings.transition,
            hook=False,
            music_path=None,
            cache_dir="",
            temp_dir=scene_temp,
            output_path=os.path.join(scene_temp, "scene.mp4"),
        )

        def scene_progress(stage, pct, message, index=index):
            if on_progress:
                overall = int(((index + (pct / 100.0)) / total) * 100)
                on_progress(stage, min(99, overall), message)

        generate_video(
            scene_settings,
            whisper_model=whisper_model,
            on_progress=scene_progress,
            on_stage=on_stage,
        )
        scene_files.append(scene_settings.output_path)

    files = list(scene_files)
    if settings.hook:
        files = [_hook_with_audio(settings)] + files

    combined = os.path.join(base_temp, "storyboard.mp4")
    _concat(files, combined, base_temp)

    final = os.path.abspath(settings.output_path)
    os.makedirs(os.path.dirname(final), exist_ok=True)
    if settings.music_path and os.path.exists(settings.music_path):
        _mix_music(combined, settings.music_path, final, settings.music_volume)
    else:
        if settings.music_path:
            print(
                f"[storyboard] music track missing, keeping narration only: "
                f"{settings.music_path}"
            )
        shutil.copy2(combined, final)

    # Creator-added timeline tracks (text, PiP/logo overlays, extra audio) are
    # applied once on the finished cut, after narration/captions/music.
    if (
        getattr(settings, "timeline_texts", None)
        or getattr(settings, "timeline_overlays", None)
        or getattr(settings, "timeline_audio", None)
    ):
        from .finish import apply_timeline

        finished = os.path.join(base_temp, "timeline_final.mp4")
        apply_timeline(final, finished, settings)
        os.replace(finished, final)

    if on_progress:
        on_progress("finalizing", 100, "Storyboard ready")
    return final
