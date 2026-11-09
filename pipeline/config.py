"""Shared configuration, presets, and settings models for the Vidyra engine."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from . import aspect as aspect_lib

# ---------------------------------------------------------------------------
# Constants (preserved from reference pipeline)
# ---------------------------------------------------------------------------

VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30
VIDEO_FORMAT = "9:16"

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".webm")
AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg")

TRANSITION_DURATION = 0.45

# Background music. The bed is loudness-normalised to MUSIC_BED_DB first, so a
# track plays at the same level whether it came from the (very quiet) built-in
# library or a user upload. The user's music_volume then sets how far under the
# narration it sits: 1.0 is MUSIC_BED_DB as-is, 0.35 (the default) lands the bed
# roughly 15 dB below the voice, which is the usual background-music level.
#
# The normalisation is a measured static gain (see audio.music_bed_volume), not
# a `loudnorm` filter: in a filter_complex graph `loudnorm` is not reproducible,
# and the bed wandered by up to 20 dB between otherwise identical renders, which
# is what made background music appear to drop out at random.
MUSIC_VOLUME = 0.35
MUSIC_VOLUME_MIN = 0.0
MUSIC_VOLUME_MAX = 1.0
MUSIC_BED_DB = -22.0
# Clamp on the corrective gain so a pathological upload cannot be boosted into
# noise, and so a hot master is still pulled down to the bed level. The negative
# side is generous because commercial masters routinely sit above -8 LUFS and
# need more than a few dB of attenuation.
MUSIC_BED_GAIN_MIN_DB = -30.0
MUSIC_BED_GAIN_MAX_DB = 24.0
# Narration is gain-corrected to this integrated loudness, so speech sits at a
# consistent level regardless of the TTS voice or provider.
VOICE_TARGET_DB = -16.0
VOICE_GAIN_MIN_DB = -24.0
VOICE_GAIN_MAX_DB = 24.0
# Sidechain ducking: how far the bed drops while someone is speaking.
#
# TUNED 2026-10-01 after measuring real narration. Every silence gap in a
# spoken track is short -- 0.10s to 0.28s between sentences, at most ~0.55s at
# a paragraph break (see PACING_SETTINGS). The previous release of 350ms was
# longer than every one of those gaps, so the bed was pushed down, started
# recovering, and got pushed down again before it ever arrived. The result
# sounded like the music flickering in and out rather than ducking under
# speech. That was a tuning fault, not a stale render or a cache: the mix is
# rebuilt from scratch on every job, and image vs video uploads take the same
# path through create_final_audio.
#
# Ratio 8.0 lands the bed roughly 9-10 dB down while narration plays, which is
# a depth you can actually hear as a duck (4.0 only gave ~4.85 dB).
#
# Release is deliberately long so the bed stays out of the way for the whole
# narration and comes back up only in genuinely long silences. This is the
# calm option: the bed does not rise and fall with every sentence. If you want
# it to breathe with the speech rhythm instead, drop the release to ~140ms.
MUSIC_DUCK_THRESHOLD = 0.05
MUSIC_DUCK_RATIO = 8.0
MUSIC_DUCK_ATTACK = 20
MUSIC_DUCK_RELEASE = 1200

# Uploaded videos are auto-split into fixed-length segments on ingest.
VIDEO_SEGMENT_SECONDS = 4.0
MAX_VIDEO_SEGMENTS = 60

# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

PACING_SETTINGS = {
    "Breaking News": {"sentence_pause": 0.08, "paragraph_pause": 0.20},
    "Normal Sports Story": {"sentence_pause": 0.18, "paragraph_pause": 0.40},
    "High Energy": {"sentence_pause": 0.04, "paragraph_pause": 0.12},
    "Analysis": {"sentence_pause": 0.28, "paragraph_pause": 0.55},
}

# ---------------------------------------------------------------------------
# Piper prosody
# ---------------------------------------------------------------------------
#
# Piper is a VITS model, and the generator's two noise knobs are what decide
# whether it sounds alive or flat:
#
#   noise_scale     — stochastic variation in the predicted spectrogram. Too
#                     high is breathy/noisy, too low is lifeless.
#   noise_w_scale   — jitter in predicted phoneme durations. This is the one
#                     that most drives the "robotic" read: high values make
#                     articulation timing inconsistent from sentence to
#                     sentence, which listeners hear as a machine rather than a
#                     speaker. Lower = crisper and steadier.
#
# Vidyra used to pass only --length-scale, so both stayed at Piper's own
# defaults for every voice. These are deliberately only slightly tighter than
# those defaults; per-voice overrides below exist because the right value is
# corpus-dependent and should be chosen by listening (see scripts/bench_voice.py).
PIPER_NOISE_SCALE = 0.6
PIPER_NOISE_W_SCALE = 0.75

# Per-voice overrides, keyed by the voice file's basename without extension
# ("en_US-lessac-high"). Voices not listed here use the defaults above.
PIPER_VOICE_PROSODY: dict = {}

# Narration post-processing. A gentle de-esser tames the sibilance that neural
# TTS over-produces, and the presence lift adds the intelligibility bite that
# compressed social-video audio loses. All static filters, so a render is
# reproducible (see the loudnorm note in audio.py).
NARRATION_DEESSER = "deesser=i=0.12:m=0.5:f=0.5:s=0"
NARRATION_PRESENCE = "equalizer=f=3200:t=q:w=1.1:g=2.0"

# Narration ends with a fixed gain to VOICE_TARGET_DB and no headroom of its
# own, so real speech transients reached 0 dBFS and clipped (measured 20 full
# scale samples on an 18s render). The music bus already limits at MIX_LIMITER;
# narration needs the same safety net, and it is what keeps the compressor
# downstream of us in storyboard/face from seeing overs.
NARRATION_LIMITER = "alimiter=limit=0.95:level=disabled"

TRANSITIONS = [
    "Auto",
    "fade",
    "slideleft",
    "slideright",
    "wipeleft",
    "wiperight",
    "zoomin",
]

CAPTION_STYLES = {
    "Karaoke": {"primary": "&H00FFFFFF", "secondary": "&H00FFFF00", "bold": "1"},
    "Clean": {"primary": "&H00FFFFFF", "secondary": "&H00FFFFFF", "bold": "1"},
    "Bold": {"primary": "&H00FFFFFF", "secondary": "&H0000FFFF", "bold": "1"},
}

CAPTION_POSITIONS = {"Top": 300, "Center": 960, "Bottom": 1580}

HOOK_STYLES = ["Breaking News", "Question", "Bold Statement", "Debate", "Game Day"]


def find_font_file() -> str:
    """Locate a usable bold TTF font, preferring DejaVu Sans Bold."""
    candidates = [
        os.environ.get("VIDYRA_FONT_FILE", ""),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return ""  # let FFmpeg fall back to its default font


# ---------------------------------------------------------------------------
# Settings model
# ---------------------------------------------------------------------------


def bounded_int(value, fallback: int, lo: int, hi: int) -> int:
    """Read an int out of untrusted input.

    Anything unusable -- a missing key, `"abc"`, a float, NaN -- yields the
    default, and so does a value outside [lo, hi]: clamping a request for a
    5000px frame down to 4096 would silently render something the creator never
    asked for, while restoring the default keeps the render legal and honest.
    """
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return fallback
    return number if lo <= number <= hi else fallback


def bounded_float(value, fallback: float, lo: float, hi: float) -> float:
    """Read a float out of untrusted input; see `bounded_int` for the policy."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:  # NaN
        return fallback
    return number if lo <= number <= hi else fallback


def _even(value: int) -> int:
    """libx264 in yuv420p rejects odd dimensions; nudge down to a legal one."""
    return value if value % 2 == 0 else max(2, value - 1)


@dataclass
class RenderSettings:
    """Everything needed to render one video from a script + media."""

    # Script / media
    script: str = ""
    media_files: list = field(default_factory=list)  # local absolute paths
    clip_durations: list = field(default_factory=list)  # per-clip seconds (None => auto)
    # Per-clip video trim in-points (seconds into source). Parallel to
    # media_files; images ignore it. None/0 => start at 0.
    trim_starts: list = field(default_factory=list)
    # Per-clip crop framing: (focus_x, focus_y) normalized 0-1 and a zoom
    # multiplier >= 1. Parallel to media_files; empty entries mean center/1x.
    clip_focus: list = field(default_factory=list)
    clip_zoom: list = field(default_factory=list)
    # Explicit scene length (seconds). 0 => narration decides the length
    # (historical behavior). >0 makes the timeline authoritative: narration is
    # padded/trimmed to fit before the visuals are laid out.
    target_duration: float = 0.0
    # Low-res fast draft. Halves the canvas and caps fps for on-demand previews.
    preview: bool = False

    # Narration
    voice_path: str = ""
    # Cloned-voice narration (phase 2): local WAV pre-synthesized by the
    # backend via the RunPod voiceclone tool. When set, the engine reuses it
    # instead of Piper. Single field — no parallel narration path.
    voice_clone_wav: str = ""
    speech_rate: float = 1.0
    # When True, the narration is taken from the media clip's own audio (used
    # e.g. for lip-synced podcast scenes) instead of synthesized by Piper.
    use_clip_audio: bool = False
    # Optional multi-character dialogue: {"speaker name (lowercase)": voice_path}.
    # Sentences written as "Name: line" are spoken with that character's voice.
    # Values may be a Piper model path or "clone:<path>" (pre-cloned voice).
    dialogue_voices: dict = field(default_factory=dict)
    # Lines already rendered in a cloned voice, keyed by the "clone:<path>"
    # voice value then the exact spoken text: {"clone:media/…": {"hi": "/tmp/x.wav"}}.
    # Produced once per voice per job (one GPU run), then reused by every scene.
    clone_line_banks: dict = field(default_factory=dict)

    # Pacing
    pacing: str = "Normal Sports Story"

    # Hook
    hook: bool = True
    hook_style: str = "Breaking News"
    hook_text: str = ""
    hook_subtext: str = ""
    hook_duration: float = 1.5
    hook_cover: str = ""  # local path to a cover image (hook background)

    # Captions
    captions: bool = True
    caption_style: str = "Karaoke"
    font_size: int = 58
    words_per_line: int = 5
    caption_position: str = "Bottom"

    # Music
    music_path: Optional[str] = None  # None => narration only
    # Bed level relative to the normalised music bed, 0.0-1.0. See MUSIC_VOLUME.
    music_volume: float = MUSIC_VOLUME

    # Visuals
    transition: str = "Auto"
    reframe: str = "auto"  # "auto" | "crop" (full-bleed) | "blur" (fit + blurred bg)

    # Page-name corner badge (@handle in the top-right)
    show_page_name: bool = True
    page_name: str = ""

    # Output / pipeline environment
    #
    # `aspect` is the user-facing shape. When it names a fixed ratio it is the
    # source of truth and recomputes width/height below, keeping the existing
    # long edge as the pixel budget so a landscape render is still a 1080p
    # deliverable. It defaults to "auto" -- the historical 9:16 canvas above --
    # so renders that never touch it are unchanged.
    aspect: str = aspect_lib.AUTO
    width: int = VIDEO_WIDTH
    height: int = VIDEO_HEIGHT
    fps: int = VIDEO_FPS
    temp_dir: str = ""
    output_path: str = ""
    font_file: str = ""
    cache_dir: str = ""  # persistent per-user dir; "" disables caching

    def __post_init__(self):
        self.apply_aspect()
        self.sanitize_numbers()
        if self.preview:
            # Draft renders only exist to check timing/framing; half resolution
            # and a 24fps cap roughly quarter the encode cost.
            self.width = _even(max(128, self.width // 2))
            self.height = _even(max(128, self.height // 2))
            self.fps = min(self.fps, 24)
        if not self.font_file:
            self.font_file = find_font_file()
        if self.temp_dir:
            os.makedirs(self.temp_dir, exist_ok=True)

    def sanitize_numbers(self):
        """Coerce every numeric setting into the range that can actually render.

        Settings arrive from the client and from stored job documents, so they
        are untrusted: `words_per_line=0` makes `range(0, n, 0)` raise, a
        non-numeric `font_size` makes `int()` raise, an odd width is rejected by
        libx264's yuv420p. Any of those would kill the render after every GPU
        run was already spent, so they are fixed once, centrally, instead of at
        the dozens of call sites.
        """
        self.font_size = bounded_int(self.font_size, 58, 8, 240)
        self.words_per_line = bounded_int(self.words_per_line, 5, 1, 20)
        self.width = _even(bounded_int(self.width, VIDEO_WIDTH, 128, 4096))
        self.height = _even(bounded_int(self.height, VIDEO_HEIGHT, 128, 4096))
        self.fps = bounded_int(self.fps, VIDEO_FPS, 1, 120)
        self.speech_rate = bounded_float(self.speech_rate, 1.0, 0.5, 2.0)
        self.hook_duration = bounded_float(self.hook_duration, 1.5, 0.0, 30.0)
        self.music_volume = bounded_float(self.music_volume, MUSIC_VOLUME, 0.0, 1.0)
        self.target_duration = bounded_float(self.target_duration, 0.0, 0.0, 600.0)

    def apply_aspect(self):
        """Re-derive width/height from `aspect` when it names a fixed ratio.

        `pipeline.aspect.resolve` keeps the longer existing edge as the pixel
        budget, so flipping 1080x1920 to 16:9 yields 1920x1088 rather than
        shrinking the deliverable to the video tier.
        """
        aspect = aspect_lib.coerce(self.aspect)
        self.aspect = aspect
        if aspect == aspect_lib.AUTO or aspect not in aspect_lib.ASPECTS:
            return
        self.width, self.height = aspect_lib.resolve_size(aspect, max(self.width, self.height))

    def caption_values(self):
        style = CAPTION_STYLES.get(self.caption_style, CAPTION_STYLES["Karaoke"])
        return {
            "primary": style["primary"],
            "secondary": style["secondary"],
            "bold": style["bold"],
            "font_size": int(self.font_size),
            "words_per_line": int(self.words_per_line),
            "position": CAPTION_POSITIONS.get(self.caption_position, 1580),
        }

    def pacing_values(self):
        return PACING_SETTINGS.get(
            self.pacing, PACING_SETTINGS["Normal Sports Story"]
        )