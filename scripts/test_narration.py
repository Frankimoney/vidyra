"""Tests for narration chunking, the clone-bank contract, and the voice bus.

Track A of the voice work replaced one-Piper-call-per-sentence with
paragraph-level chunks and added prosody knobs, a de-esser/presence lift and a
limiter. Each of those is a contract something else depends on, so they are
pinned here:

  - the chunker is the single source of truth for what gets synthesized
  - `clone_lines_for` must emit exactly the keys `create_narration` looks up,
    or every pre-rendered cloned line misses the bank and the job fails
  - two characters sharing one voice must not merge into one chunk
  - narration must not clip (the limiter was added after 20 full-scale samples
    were measured on a real 18s render)

Needs ffmpeg on PATH and a Piper voice in models/piper.
Run:  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/test_narration.py
"""

import os
import shutil
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import piper_synth  # noqa: E402
from pipeline.config import (  # noqa: E402
    NARRATION_LIMITER,
    PIPER_NOISE_SCALE,
    PIPER_NOISE_W_SCALE,
)
from pipeline.narration import (  # noqa: E402
    clone_lines_for,
    create_narration,
    narration_chunks,
    parse_dialogue,
    piper_prosody,
    split_script,
    synth_chunk,
)

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


VOICE = "models/piper/en_US-lessac-medium.onnx"
CLONE_A = "clone:/refs/ada.wav"
CLONE_B = "clone:/refs/ryan.wav"


def test_chunking():
    print("chunking:")
    script = (
        "The game was tied late. Then the goal came.\n"
        "\n"
        "Nothing else mattered after that."
    )
    chunks = narration_chunks(script, {}, "")
    check("one chunk per paragraph", len(chunks) == 2, str([c[1] for c in chunks]))
    check(
        "sentences inside a paragraph are joined",
        chunks[0][1] == "The game was tied late. Then the goal came.",
        chunks[0][1],
    )
    check("no chunk spans the paragraph break", len(chunks) == 2)
    check(
        "no sentence is lost",
        " ".join(c[1] for c in chunks) == " ".join(split_script(script)),
    )
    check("paragraph end is flagged", [c[2] for c in chunks] == [True, True])

    voiced = "Ada: We had nothing. Ryan: Not true!"
    chunks = narration_chunks(voiced, {"ada": CLONE_A, "ryan": CLONE_B}, "")
    check("speaker change splits even mid-paragraph", len(chunks) == 2,
          str([c[1] for c in chunks]))
    check("each speaker keeps its own voice",
          [c[0] for c in chunks] == [CLONE_A, CLONE_B])

    # The bug this guards: two characters deliberately given one Piper voice
    # must still be separated, or their lines run together with only a
    # sentence-length pause between them.
    shared = narration_chunks(voiced, {"ada": "m.onnx", "ryan": "m.onnx"}, "m.onnx")
    check("shared voice still splits by speaker", len(shared) == 2,
          str([c[1] for c in shared]))
    check("shared voice keeps the voice", all(c[0] == "m.onnx" for c in shared))

    mixed = narration_chunks(
        "Intro line here. Ada: Her line. Outro line here.",
        {"ada": CLONE_A},
        "m.onnx",
    )
    check("narrator resumes after a speaker", [c[0] for c in mixed] ==
          ["m.onnx", CLONE_A, "m.onnx"], str([c[0] for c in mixed]))

    check("empty script yields no chunks", narration_chunks("", {}, "") == [])
    check("unknown speaker is left as narration",
          parse_dialogue("Zed: hi", {"ada": CLONE_A}) == (None, "Zed: hi"))


def test_clone_contract():
    print("clone bank contract:")
    # Dialogue is "Name: text" -- without the colon these are narration lines
    # and belong to the narrator, not to a character.
    script = (
        "The first half was scrappy. Nobody settled it early.\n"
        "\n"
        "Ada: She had a point about the first half. She repeated it twice.\n"
        "Ryan: He disagreed, and he was louder about it."
    )
    voices = {"ada": CLONE_A, "ryan": CLONE_B}
    chunks = narration_chunks(script, voices, "m.onnx")

    cloned_texts = [t for v, t, _e in chunks if v.startswith("clone:")]
    banks = clone_lines_for(script, voices, "m.onnx")

    check("one bank per cloned voice", sorted(banks) == sorted([CLONE_A, CLONE_B]),
          str(sorted(banks)))
    check(
        "bank keys are exactly the cloned chunk texts",
        sorted(x for v in banks.values() for x in v) == sorted(cloned_texts),
        str(sorted(cloned_texts)),
    )
    check("narration voice is not cloned", CLONE_A not in clone_lines_for(
        "Only narration.", {}, "m.onnx"))
    check("plain Piper voice produces no bank", clone_lines_for(
        script, voices, "m.onnx").get("m.onnx") is None)


def test_prosody_settings():
    print("prosody settings:")
    check("defaults are applied", piper_prosody(VOICE) ==
          (PIPER_NOISE_SCALE, PIPER_NOISE_W_SCALE))
    check("noise is not disabled by default", PIPER_NOISE_SCALE > 0)
    out = os.path.join("storage", "_tn.wav")
    os.makedirs("storage", exist_ok=True)
    synth_chunk("A short test line.", VOICE, out, speech_rate=1.0,
                sentence_pause=0.2)
    check("synth_chunk writes a wav", os.path.exists(out) and
          os.path.getsize(out) > 1000)

    # determininistic=True must silence the graph's RandomNormalLike, which is
    # the only way to get a reproducible render.
    a = piper_synth.synthesize("Same text.", VOICE, "storage/_tn_det1.wav",
                               deterministic=True)
    b = piper_synth.synthesize("Same text.", VOICE, "storage/_tn_det2.wav",
                               deterministic=True)
    check("deterministic=True is byte reproducible",
          open(a, "rb").read() == open(b, "rb").read())
    for p in (a, b, out):
        if os.path.exists(p):
            os.remove(p)


class _Settings:
    def __init__(self, script, voice, temp_dir):
        self.script = script
        self.voice_path = voice
        self.speech_rate = 1.0
        self.pacing = "Normal Sports Story"
        self.dialogue_voices = {}
        self.clone_line_banks = {}
        self.temp_dir = temp_dir


def test_render():
    print("full narration render:")
    if not os.path.exists(VOICE):
        check("voice available", False, VOICE)
        return

    # Deliberately a *relative* path: the concat demuxer resolves relative
    # entries against the listing file's own directory, so a relative temp_dir
    # used to double the path and fail to open the chunks. WORK_ROOT defaults
    # to the relative "storage/work", so this is the production shape.
    temp_dir = os.path.join("storage", "_test_narration")
    shutil.rmtree(temp_dir, ignore_errors=True)
    os.makedirs(temp_dir, exist_ok=True)
    script = (
        "The stadium was on its feet. Nobody expected it after the first half.\n"
        "\n"
        "Then the whistle went, and the whole place collapsed into noise."
    )
    out = os.path.join(temp_dir, "narration.wav")
    create_narration(_Settings(script, VOICE, temp_dir), out)

    check("narration written", os.path.exists(out))
    w = wave.open(out, "rb")
    frames, rate = w.getnframes(), w.getframerate()
    import array
    data = array.array("h")
    data.frombytes(w.readframes(frames))
    w.close()

    duration = frames / rate
    check("duration covers the script", duration > 5, f"{duration:.2f}s")
    check("rate is 48kHz", rate == 48000, str(rate))

    full = sum(1 for x in data if abs(x) >= 32767)
    check("narration does not clip", full == 0,
          f"{full} full-scale samples" if full else "0 full-scale samples")
    peak = max((abs(x) for x in data), default=0)
    check("peak is below full scale", peak < 32767, f"peak={peak}")

    check("narration chain limits", "limit=" in NARRATION_LIMITER)

    # A chunk file must never be silently dropped by the concat demuxer.
    chunks = narration_chunks(script, {}, "")
    check("chunk files were written",
          sum(1 for n in os.listdir(temp_dir) if n.startswith("chunk_") and
              n.endswith(".wav")) >= len(chunks))

    shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    test_chunking()
    test_clone_contract()
    test_prosody_settings()
    test_render()
    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} -> {FAILED}")
        return 1
    print("ALL NARRATION TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
