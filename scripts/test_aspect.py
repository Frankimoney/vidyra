"""Local checks for app-wide aspect-ratio handling.

Vidyra renders in a dozen places -- SDXL stills, Wan motion, video composites,
Auto Film storyboards, podcasts -- and each used to hardcode its own idea of
the frame. These checks pin the one shared registry so a ratio chosen in the UI
means the same thing everywhere, and so the defaults stay exactly what they were.

No GPU needed. Run: python scripts/test_aspect.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "backend")):
    if path not in sys.path:
        sys.path.insert(0, path)

from fastapi import HTTPException  # noqa: E402

from pipeline import aspect as aspect_lib  # noqa: E402
from pipeline.config import RenderSettings  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        failures.append(name)


def section(title: str) -> None:
    print(f"\n{title}")


# ---------------------------------------------------------------------------
section("Every ratio is known, and 'auto' means 'do not touch the default'")

EXPECTED = ("auto", "9:16", "16:9", "1:1", "4:5", "5:4", "3:4", "4:3", "3:2", "2:3", "21:9")
check("ratio list matches the product surface", tuple(aspect_lib.ASPECT_CHOICES) == EXPECTED,
      str(aspect_lib.ASPECT_CHOICES))
check("every ratio has a label",
      all(aspect_lib.ASPECT_LABELS.get(r) for r in aspect_lib.ASPECT_CHOICES))
check("'auto' is recognised", aspect_lib.is_valid("auto"))
check("'7:3' is rejected", not aspect_lib.is_valid("7:3"))
check("'banana' is rejected", not aspect_lib.is_valid("banana"))
check("'9x16' is rejected", not aspect_lib.is_valid("9x16"))
check("'16:9 ' with a trailing space is accepted", aspect_lib.is_valid(" 16:9 "))
check("prompt phrase names the shape",
      aspect_lib.prompt_phrase("16:9").startswith("16:9")
      and "frame" in aspect_lib.prompt_phrase("16:9"))
check("auto contributes no shape wording of its own",
      aspect_lib.prompt_phrase("auto") == "",
      repr(aspect_lib.prompt_phrase("auto")))
check("a blank prompt phrase falls back rather than guessing",
      (aspect_lib.prompt_phrase("auto") or "9:16 vertical") == "9:16 vertical")

# ---------------------------------------------------------------------------
section("Geometry: 16px grid, correct orientation, tight ratio error")

for ratio, budget, expect_portrait in (
    ("9:16", 1920, True),
    ("16:9", 1920, False),
    ("1:1", 1024, False),
    ("4:5", 1280, True),
    ("21:9", 1280, False),
    ("2:3", 832, True),
):
    w, h = aspect_lib.resolve_size(ratio, budget)
    rw, rh = (int(x) for x in ratio.split(":"))
    actual = w / float(h)
    target = rw / float(rh)
    drift = abs(actual - target) / target * 100
    check(f"{ratio} at {budget}px -> {w}x{h} is on the 16px grid",
          w % 16 == 0 and h % 16 == 0, f"{w}x{h}")
    check(f"{ratio} at {budget}px orientation is right",
          (h > w) is expect_portrait, f"{w}x{h}")
    check(f"{ratio} at {budget}px keeps its long side at {budget}",
          max(w, h) == budget, f"{w}x{h}")
    check(f"{ratio} at {budget}px ratio error under 2% ({drift:.2f}%)", drift < 2.0, f"{drift:.2f}%")

w, h = aspect_lib.resolve_size("1:1", 1080)
check("square is exactly square despite 1080 being off-grid", w == h, f"{w}x{h}")

check("nearest_aspect reads a portrait frame as 9:16",
      aspect_lib.nearest_aspect(500, 900) == "9:16", aspect_lib.nearest_aspect(500, 900))
check("nearest_aspect reads a wide frame as 16:9",
      aspect_lib.nearest_aspect(1920, 1080) == "16:9", aspect_lib.nearest_aspect(1920, 1080))

# ---------------------------------------------------------------------------
section("Video pipeline defaults are unchanged unless a ratio is asked for")

base = RenderSettings()
check("default canvas is still the vertical 1080x1920", (base.width, base.height) == (1080, 1920),
      f"{base.width}x{base.height}")
check("default aspect is auto", base.aspect == aspect_lib.AUTO, base.aspect)

auto_settings = RenderSettings(aspect="auto")
check("'auto' keeps the legacy canvas", (auto_settings.width, auto_settings.height) == (1080, 1920),
      f"{auto_settings.width}x{auto_settings.height}")

wide = RenderSettings(aspect="16:9")
check("16:9 turns the composite landscape", wide.width > wide.height, f"{wide.width}x{wide.height}")

square = RenderSettings(aspect="1:1")
check("1:1 turns the composite square", square.width == square.height, f"{square.width}x{square.height}")

flipped = RenderSettings(aspect="4:5")
flipped.apply_aspect()
check("apply_aspect reshapes the default canvas", flipped.height > flipped.width,
      f"{flipped.width}x{flipped.height}")
check("apply_aspect keeps the 1920px long side", flipped.height == 1920, str(flipped.height))

untouched = RenderSettings(aspect="auto")
untouched.apply_aspect()
check("apply_aspect on 'auto' changes nothing", (untouched.width, untouched.height) == (1080, 1920),
      f"{untouched.width}x{untouched.height}")

# ---------------------------------------------------------------------------
section("Studio: aspect resolves to a real canvas, junk is rejected")

from app.routes import studio as studio_routes  # noqa: E402

WAN_DEFAULTS = {"aspect": "auto", "fit": "crop", "width": 0, "height": 0,
                "duration": 10, "fps": 0, "steps": 6, "cfg": 1.0, "seed": 0}

check("auto leaves the model's tuned canvas alone",
      studio_routes._apply_aspect("motion", "wan-animate-2",
                                  {"aspect": "auto", "width": 0, "height": 0})
      == {"aspect": "auto", "width": 0, "height": 0})

wan_wide = studio_routes._apply_aspect("motion", "wan-animate-2", {"aspect": "16:9", "width": 0, "height": 0})
check("Wan with 0x0 defaults gets the video tier, not a crash",
      max(wan_wide["width"], wan_wide["height"]) == aspect_lib.VIDEO_TIERS["sd"],
      f"{wan_wide['width']}x{wan_wide['height']}")
check("Wan 16:9 is landscape", wan_wide["width"] > wan_wide["height"],
      f"{wan_wide['width']}x{wan_wide['height']}")

sdxl = studio_routes._apply_aspect("txt2img", "sdxl", {"aspect": "16:9", "width": 1024, "height": 1024})
check("a fixed ratio keeps the existing pixel budget", max(sdxl["width"], sdxl["height"]) == 1024,
      f"{sdxl['width']}x{sdxl['height']}")
check("a fixed ratio overrides the model's square default", sdxl["width"] > sdxl["height"],
      f"{sdxl['width']}x{sdxl['height']}")

try:
    studio_routes._apply_aspect("motion", "wan-animate-2", {"aspect": "7:3", "width": 0, "height": 0})
    check("an unknown ratio is rejected with 400", False, "no error raised")
except HTTPException as exc:
    check("an unknown ratio is rejected with 400", exc.status_code == 400, str(exc.status_code))

check("enum params are validated, not passed through",
      studio_routes._normalize_params({"fit": "pad"}, WAN_DEFAULTS)["fit"] == "pad")
for bad in ({"fit": "zoom"}, {"aspect": "7:3"}):
    try:
        studio_routes._normalize_params(bad, WAN_DEFAULTS)
        check(f"{bad} is rejected", False, "no error raised")
    except HTTPException as exc:
        check(f"{bad} is rejected", exc.status_code == 400, str(exc.status_code))

check("catalog exposes aspect choices for the UI",
      tuple(studio_routes.STUDIO_ENUM_VALUES["aspect"]) == EXPECTED)
check("catalog exposes both fit modes", tuple(studio_routes.STUDIO_ENUM_VALUES["fit"]) == ("crop", "pad"))

# ---------------------------------------------------------------------------
section("Auto Film: one shape for the stills and the cut")

from app.workers.film_worker import DEFAULT_FILM_ASPECT  # noqa: E402

check("film default is 9:16", DEFAULT_FILM_ASPECT == "9:16", DEFAULT_FILM_ASPECT)

for ratio, expect_portrait in (("9:16", True), ("16:9", False), ("1:1", False), ("4:5", True)):
    still = aspect_lib.resolve_size(ratio, 1024)
    video = aspect_lib.resolve_size(ratio, 1920)
    check(f"{ratio} still and video agree on orientation",
          (still[1] > still[0]) is (video[1] > video[0]) is expect_portrait,
          f"still {still}, video {video}")

from app.routes.films import _validate_aspect  # noqa: E402

check("film validates a good ratio", _validate_aspect("16:9") == "16:9")
check("film treats blank as auto", _validate_aspect("") == aspect_lib.AUTO)
try:
    _validate_aspect("7:3")
    check("film rejects an unknown ratio", False, "no error raised")
except HTTPException as exc:
    check("film rejects an unknown ratio", exc.status_code == 400, str(exc.status_code))

from app.models import FilmCreate, PodcastCreate  # noqa: E402

check("FilmCreate accepts an aspect", FilmCreate(idea="x", aspect="16:9").aspect == "16:9")
check("PodcastCreate accepts an aspect", PodcastCreate(topic="x", aspect="1:1").aspect == "1:1")
check("FilmCreate aspect defaults to auto", FilmCreate(idea="x").aspect == aspect_lib.AUTO)

# ---------------------------------------------------------------------------
section("Prompts follow the chosen shape instead of saying 9:16")

from app.services import scriptwriter  # noqa: E402


class _Settings:
    gemini_configured = False
    llm_configured = False
    film_max_scenes = 16
    podcast_max_turns = 12


settings_stub = _Settings()
for ratio in ("9:16", "16:9", "1:1", "21:9"):
    film = scriptwriter.write_film_script("a runner crosses a wet street", "cinematic",
                                          2, [], settings_stub, aspect=ratio)
    podcast = scriptwriter.write_podcast_script("a wild week in sports", [], 2,
                                                settings_stub, aspect=ratio)
    check(f"{ratio} film scene prompt names the shape",
          aspect_lib.prompt_phrase(ratio) in film["scenes"][0]["setting"])
    check(f"{ratio} podcast turn prompt names the shape",
          aspect_lib.prompt_phrase(ratio) in podcast["turns"][0]["visual"])

check("hook prompt takes the shape",
      "16:9" in scriptwriter._HOOK_SYSTEM.format(shape=aspect_lib.prompt_phrase("16:9").split(" frame")[0]))

from app.services import package as package_service  # noqa: E402

system = package_service._SYSTEM.replace("__SHAPE__", aspect_lib.prompt_phrase("21:9"))
check("package prompt takes the shape", "21:9" in system and "9:16 vertical" not in system)

# ---------------------------------------------------------------------------
section("Wan pad mode fits the subject instead of cropping it")

worker_path = ROOT / "deploy" / "runpod" / "worker.py"
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("runpod_worker_aspect", worker_path)
worker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(worker)

pad_filter = worker._pad_filter(832, 1248, 832, 468)
check("pad keeps the source whole (fit, not fill)",
      "force_original_aspect_ratio=decrease" in pad_filter)
check("pad never upscales past the output box",
      f"w={832}" in pad_filter and f"h={468}" in pad_filter)
check("pad centres the subject", "(W-w)/2:(H-h)/2" in pad_filter)
check("pad fills the remainder", "pad=832:468" in pad_filter)

check("fit_dimensions follows a portrait source", worker.fit_dimensions(832, 1248)[1] > worker.fit_dimensions(832, 1248)[0])
check("fit_dimensions follows a landscape source", worker.fit_dimensions(1920, 1080)[0] > worker.fit_dimensions(1920, 1080)[1])
check("fit_dimensions survives an unknown source size", worker.fit_dimensions(0, 0) == (832, 480))

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("All aspect-ratio checks passed.")