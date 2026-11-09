"""Video generation orchestrator — the reference `generate_video` flow."""

from __future__ import annotations

import os
import shutil

from .audio import create_final_audio
from .captions import create_captions
from .hook import build_hook, create_hook
from .narration import create_narration
from .render import concat_visuals, render_final
from .transitions import create_clip_sequence
from .utils import get_duration, classify_media, run_cmd, PipelineError

STAGES = [
    "preparing",
    "narration",
    "visuals",
    "captions",
    "audio",
    "finalizing",
]

STAGE_WEIGHTS = {
    "preparing": 5,
    "narration": 25,
    "visuals": 45,
    "captions": 12,
    "audio": 8,
    "finalizing": 5,
}


class ProgressReporter:
    """Coarse progress reporting. We only report what we actually know."""

    def __init__(self, on_progress=None, on_stage=None):
        self.on_progress = on_progress
        self.on_stage = on_stage
        self._stage_floor = 0

    def stage_start(self, stage):
        self._stage_floor = sum(
            v for k, v in STAGE_WEIGHTS.items()
            if STAGES.index(k) < STAGES.index(stage)
        )
        if self.on_stage:
            self.on_stage(stage)
        self.update(stage, 0, "")

    def update(self, stage, within_pct, message):
        span = STAGE_WEIGHTS.get(stage, 0)
        total = self._stage_floor + int((within_pct / 100) * span)
        total = min(99, total)
        if self.on_progress:
            self.on_progress(stage, total, message)


def _extract_clip_audio(settings, output_path):
    """Reuse a video media clip's own audio as the narration track.

    Returns the extracted WAV path on success, or None so the caller falls
    back to Piper narration.
    """
    source = (settings.media_files or [None])[0]
    if not source or not os.path.exists(source) or classify_media(source) != "video":
        return None
    try:
        run_cmd(
            [
                "ffmpeg", "-y",
                "-i", source,
                "-map", "a:0",
                "-vn",
                "-ac", "2",
                "-ar", "48000",
                output_path,
            ]
        )
        if os.path.exists(output_path) and get_duration(output_path) > 0.1:
            return output_path
    except Exception:  # noqa: BLE001 — fall back to synthesized narration
        return None
    return None


def _fit_narration(path: str, target: float, settings) -> str:
    """Make a narration WAV exactly `target` seconds long.

    Used only when a scene carries an explicit duration (timeline editor). The
    audio is authoritative for sync, so the fit happens BEFORE captions and
    visuals: padding adds silence, a mild overrun is sped up, and a large
    overrun is trimmed with a short fade so a cut is not audible.
    """
    actual = get_duration(path)
    if actual <= 0 or target <= 0:
        return path
    if abs(actual - target) < 0.05:
        return path

    out = os.path.join(settings.temp_dir, "narration_fit.wav")
    common = [
        "ffmpeg", "-y", "-i", path,
        "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
    ]
    if actual < target:
        run_cmd(common + ["-af", "apad", "-t", f"{target:.3f}", out])
        return out

    ratio = actual / target
    if ratio <= 1.15:
        # Short overrun: speed up rather than cutting a sentence in half.
        run_cmd(
            common
            + ["-af", f"atempo={ratio:.4f}", "-t", f"{target:.3f}", out]
        )
        return out

    fade = min(0.15, target * 0.1)
    run_cmd(
        common
        + [
            "-af", f"afade=t=out:st={max(0.0, target - fade):.3f}:d={fade:.3f}",
            "-t", f"{target:.3f}",
            out,
        ]
    )
    return out


def generate_video(settings, whisper_model=None, on_progress=None, on_stage=None):
    """Run the full pipeline and return the final MP4 path.

    settings: RenderSettings with local media paths already staged in
              settings.media_files and output_path set.
    """
    reporter = ProgressReporter(on_progress, on_stage)
    reporter.stage_start("preparing")

    script = (settings.script or "").strip()
    if not script:
        raise PipelineError("Please enter a script.")

    if not settings.media_files:
        raise PipelineError("Please upload at least one image or video.")

    # Work with absolute paths so FFmpeg concat lists never resolve
    # relative paths against the list file's own directory.
    settings.temp_dir = os.path.abspath(settings.temp_dir)
    settings.output_path = os.path.abspath(settings.output_path)
    os.makedirs(settings.temp_dir, exist_ok=True)

    # ------------------------------------------------------------
    # NARRATION
    # ------------------------------------------------------------
    reporter.stage_start("narration")
    narration_output = os.path.join(settings.temp_dir, "narration.wav")

    def _create_narration():
        with_rep = reporter.update
        return create_narration(
            settings,
            output_path=narration_output,
            on_progress=with_rep,
        )

    clone_wav = (getattr(settings, "voice_clone_wav", "") or "").strip()
    if clone_wav and os.path.isfile(clone_wav):
        # Cloned voice (phase 2): reuse the pre-synthesized track instead of
        # Piper. Speech-rate applied with atempo so pacing sliders keep working.
        rate = 1.0
        try:
            rate = float(getattr(settings, "speech_rate", 1.0) or 1.0)
        except (TypeError, ValueError):
            rate = 1.0
        if 0.85 <= rate <= 1.15 and abs(rate - 1.0) > 0.001:
            from .utils import run_cmd as _run

            _run(
                [
                    "ffmpeg", "-y",
                    "-i", clone_wav,
                    "-filter:a", f"atempo={rate:.4f}",
                    "-ar", "48000", "-ac", "1",
                    "-c:a", "pcm_s16le",
                    narration_output,
                ]
            )
        else:
            shutil.copy2(clone_wav, narration_output)
        reporter.update("narration", 100, "Cloned narration ready")
        narration = narration_output
    elif getattr(settings, "use_clip_audio", False):
        # The media clip already carries spoken audio (lip-synced host lines),
        # so reuse it as the scene's narration instead of synthesizing.
        narration = narration_output
        extracted = _extract_clip_audio(settings, narration)
        if not extracted:
            narration = _create_narration()
        else:
            reporter.update("narration", 100, "Narration from clip audio")
    else:
        cache_key = None
        if getattr(settings, "cache_dir", ""):
            from .cache import cache_get, cache_put, narration_key

            cache_key = narration_key(settings)
            cached = cache_get(settings.cache_dir, cache_key, "nar", "wav")
            if cached:
                shutil.copy2(cached, narration_output)
                reporter.update("narration", 100, "Narration reused from cache")
                narration = narration_output
            else:
                narration = _create_narration()
                cache_put(narration, settings.cache_dir, cache_key, "nar", "wav")
                reporter.update("narration", 100, "Narration complete")
        else:
            narration = _create_narration()
            reporter.update("narration", 100, "Narration complete")

    # Timeline-authoritative scenes carry an explicit length; fit the spoken
    # track to it before anything is timed against the audio.
    target = float(getattr(settings, "target_duration", 0.0) or 0.0)
    if target > 0.05:
        narration = _fit_narration(narration, target, settings)
    narration_duration = get_duration(narration)

    # ------------------------------------------------------------
    # VISUAL TRACK
    # ------------------------------------------------------------
    reporter.stage_start("visuals")
    kinds = [classify_media(p) or "image" for p in settings.media_files]
    visual = create_clip_sequence(
        settings.media_files,
        narration_duration,
        settings,
        clip_durations=settings.clip_durations or None,
        kinds=kinds,
        trim_starts=getattr(settings, "trim_starts", None),
        focuses=getattr(settings, "clip_focus", None),
        zooms=getattr(settings, "clip_zoom", None),
    )
    reporter.update("visuals", 85, "Visual track ready")

    # ------------------------------------------------------------
    # HOOK
    # ------------------------------------------------------------
    final_visual = visual
    if settings.hook:
        final_hook_text, final_hook_subtext = build_hook(
            settings.hook_style, settings.hook_text, settings.hook_subtext
        )
        hook_file = create_hook(
            final_hook_text, final_hook_subtext, settings.hook_duration, settings
        )
        final_visual = concat_visuals(hook_file, visual, settings)
    reporter.update("visuals", 100, "Visuals complete")

    # ------------------------------------------------------------
    # CAPTIONS
    # ------------------------------------------------------------
    reporter.stage_start("captions")
    caption_file = None
    if settings.captions:
        if whisper_model is None:
            import whisper  # type: ignore

            whisper_model = whisper.load_model("base")
        caption_file = create_captions(narration, whisper_model, settings)
    reporter.update("captions", 100, "Captions complete")

    # ------------------------------------------------------------
    # AUDIO
    # ------------------------------------------------------------
    reporter.stage_start("audio")
    final_audio = create_final_audio(
        narration,
        settings.music_path,
        settings.hook,
        settings.hook_duration,
        settings,
    )
    reporter.update("audio", 100, "Audio mix complete")

    # ------------------------------------------------------------
    # FINAL RENDER
    # ------------------------------------------------------------
    reporter.stage_start("finalizing")
    output = render_final(
        final_visual,
        final_audio,
        caption_file,
        settings.output_path,
        settings,
    )

    if not os.path.exists(output):
        raise PipelineError("Final video was not created.")

    reporter.update("finalizing", 100, "Video ready")
    return output