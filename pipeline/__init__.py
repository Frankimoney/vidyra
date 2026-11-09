"""Vidyra video engine.

Extracted from the working Colab reference pipeline
(Sc_Gamecocks_Video_Pipeline). The processing logic is preserved as-is;
only paths, voice/preferences, and reporting are parameterized.
"""

from .engine import generate_video, ProgressReporter
from .config import (
    RenderSettings,
    PACING_SETTINGS,
    HOOK_STYLES,
    CAPTION_STYLES,
    CAPTION_POSITIONS,
    TRANSITIONS,
)

__all__ = [
    "generate_video",
    "ProgressReporter",
    "RenderSettings",
    "PACING_SETTINGS",
    "HOOK_STYLES",
    "CAPTION_STYLES",
    "CAPTION_POSITIONS",
    "TRANSITIONS",
]