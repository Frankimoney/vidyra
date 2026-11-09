"""Output aspect ratios, shared by every render surface.

One registry so "16:9" means the same pixels in the Studio tools, in an
Auto Film, in a Storyboard and in the final composite. Nothing else in the
codebase is allowed to invent its own idea of a shape:

  * :data:`ASPECTS` is the allow-list of ratios a user may pick.
  * :func:`resolve_size` turns a ratio plus a resolution tier into concrete
    width/height, snapped to the grid the diffusion models need.
  * :func:`nearest_aspect` goes the other way, so a caller that only knows
    pixel dimensions (a probed upload, an existing preset) can label itself.
  * :func:`prompt_phrase` keeps the image prompts in the same language as the
    canvas instead of hardcoding "9:16 vertical" everywhere.

Grid rules, because the models are strict about it:

  * Wan (and the VAE behind it) wants spatial dimensions on a **16px** grid;
    ``deploy/runpod/worker.py::_snap`` does the same thing at render time.
  * Everything downstream of us encodes with yuv420p, which needs **even**
    dimensions. A multiple of 16 is even, so one grid covers both.
"""

from __future__ import annotations

GRID = 16

#: Ratio key -> (numerator, denominator). Ordered "social habit" first so the
#: dropdowns read sensibly: the shapes people actually publish to.
ASPECTS: dict[str, tuple[int, int]] = {
    "9:16": (9, 16),
    "16:9": (16, 9),
    "1:1": (1, 1),
    "4:5": (4, 5),
    "5:4": (5, 4),
    "3:4": (3, 4),
    "4:3": (4, 3),
    "3:2": (3, 2),
    "2:3": (2, 3),
    "21:9": (21, 9),
}

#: "auto" is always offered and means "keep whatever the caller already had"
#: (a model's tuned default, or the driving clip's orientation for motion).
AUTO = "auto"

ASPECT_CHOICES: tuple[str, ...] = (AUTO,) + tuple(ASPECTS)

ASPECT_LABELS: dict[str, str] = {
    AUTO: "Auto (model default)",
    "9:16": "9:16 vertical (Reels / Shorts / TikTok)",
    "16:9": "16:9 widescreen (YouTube)",
    "1:1": "1:1 square (feed post)",
    "4:5": "4:5 portrait (feed post)",
    "5:4": "5:4 landscape",
    "3:4": "3:4 portrait",
    "4:3": "4:3 (classic TV)",
    "3:2": "3:2 landscape",
    "2:3": "2:3 portrait",
    "21:9": "21:9 ultrawide (cinematic)",
}

#: How the shape is described to an image model. Prompting a diffusion model
#: for "16:9 widescreen frame" gets a composed shot; prompting it for "9:16"
#: when the canvas is 16:9 gets a tall subject letterboxed into black.
_PROMPT_PHRASES: dict[str, str] = {
    "9:16": "9:16 vertical frame",
    "16:9": "16:9 widescreen frame",
    "1:1": "1:1 square frame",
    "4:5": "4:5 vertical frame",
    "5:4": "5:4 horizontal frame",
    "3:4": "3:4 vertical frame",
    "4:3": "4:3 horizontal frame",
    "3:2": "3:2 horizontal frame",
    "2:3": "2:3 vertical frame",
    "21:9": "21:9 ultrawide cinematic frame",
}

#: Long-side pixels per tier. The video tiers bracket what Wan is comfortable
#: with (its distilled LoRA is a 480p model); the image tiers bracket what SDXL
#: and Flux are comfortable with.
VIDEO_TIERS: dict[str, int] = {"sd": 832, "hd": 1280}
IMAGE_TIERS: dict[str, int] = {"md": 1024, "lg": 1536}

#: Output canvas for the final composite, per ratio, at 1080-class long side.
#: The pipeline renders 1080p regardless of orientation so a landscape upload
#: is still a 1080p deliverable.
COMPOSITE_LONG_SIDE = 1920


def snap(value: float, grid: int = GRID, minimum: int | None = None) -> int:
    """Round to the nearest multiple of ``grid`` (never below ``minimum``)."""
    lo = grid if minimum is None else max(grid, minimum)
    snapped = int(round(float(value) / grid)) * grid
    return max(lo, snapped)


def is_valid(aspect: str | None) -> bool:
    """Is this one of the ratios we support?

    Surrounding whitespace is ignored: these strings arrive from HTML selects,
    query strings and JSON bodies, and a stray space is a typo, not a request
    for a different shape.
    """
    value = (aspect or "").strip()
    return bool(value) and (value == AUTO or value in ASPECTS)


def coerce(aspect: str | None, default: str = AUTO) -> str:
    """Return a supported ratio, falling back to ``default`` for junk input."""
    value = (aspect or "").strip()
    return value if is_valid(value) else default


def resolve_size(aspect: str, long_side: int, grid: int = GRID) -> tuple[int, int]:
    """Concrete (width, height) for ``aspect`` at the given long-side size.

    ``long_side`` is the pixel budget for the *longer* edge, so the caller can
    keep a model's quality target (832 for Wan video, 1024 for SDXL) while
    changing the shape.
    """
    if aspect == AUTO or aspect not in ASPECTS:
        raise ValueError(f"not a fixed aspect ratio: {aspect!r}")
    num, den = ASPECTS[aspect]
    long_side = max(grid * 2, snap(long_side, grid))
    short_side = snap(long_side * min(num, den) / max(num, den), grid)
    return (long_side, short_side) if num >= den else (short_side, long_side)


def resolve(aspect: str, width: int, height: int, grid: int = GRID) -> tuple[int, int]:
    """Re-shape an existing (width, height) into ``aspect``, keeping its scale.

    The pixel budget is taken from the longer existing edge, so switching a
    1024x1024 preset to 16:9 gives 1024x576 rather than silently dropping the
    model to the video tier.
    """
    width, height = int(width or 0), int(height or 0)
    long_side = snap(max(width, height) or IMAGE_TIERS["md"], grid)
    return resolve_size(aspect, long_side, grid)


def ratio(width: float, height: float) -> float:
    return (float(width) / float(height)) if height else 0.0


def nearest_aspect(width: int, height: int, tolerance: float = 0.06) -> str:
    """Label for a pixel size, e.g. (832, 480) -> "16:9".

    ``tolerance`` is a relative slack, matching how ``pipeline.visuals``
    decides whether a source already matches the canvas.
    """
    if not width or not height:
        return AUTO
    target = ratio(width, height)
    best_key, best_delta = AUTO, None
    for key, (num, den) in ASPECTS.items():
        delta = abs(ratio(num, den) - target) / max(target, 1e-6)
        if best_delta is None or delta < best_delta:
            best_key, best_delta = key, delta
    return best_key if best_delta is not None and best_delta <= tolerance else AUTO


def is_portrait(width: int, height: int) -> bool:
    return int(height or 0) > int(width or 0)


def prompt_phrase(aspect: str) -> str:
    """Aspect wording to splice into an image prompt.

    Unknown ratios get a computed phrase rather than a silent "9:16", because
    a wrong shape word in the prompt is what makes a landscape render look
    like a tall shot with empty sides.
    """
    aspect = coerce(aspect)
    if aspect in _PROMPT_PHRASES:
        return _PROMPT_PHRASES[aspect]
    num, den = ASPECTS.get(aspect, (0, 0))
    if not num:
        return ""
    word = "vertical" if den > num else ("square frame" if num == den else "horizontal")
    return f"{num}:{den} {word} frame"


def catalog() -> dict:
    """Serialisable description of the registry, for the Studio catalog."""
    return {
        "choices": list(ASPECT_CHOICES),
        "labels": dict(ASPECT_LABELS),
        "videoTiers": dict(VIDEO_TIERS),
        "imageTiers": dict(IMAGE_TIERS),
    }