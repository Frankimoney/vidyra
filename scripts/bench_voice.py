"""A/B harness for narration quality — the numbers cannot settle "sounds robotic".

Track A of the voice work changed three things at once, and only listening can
tell whether they helped:

  chunking   paragraph-level synthesis instead of one Piper call per sentence,
             so intonation carries across sentence boundaries
  prosody     --noise-scale / --noise-w-scale, previously left at Piper defaults
  polish     de-esser + presence lift on the narration bus

This renders the same script under each variant into one folder so they can be
compared by ear, and prints the mechanical differences (call count, duration,
peak level) that are worth sanity-checking alongside the listening.

Run:  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/bench_voice.py
      PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/bench_voice.py --listen
"""

import argparse
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline import piper_synth  # noqa: E402
from pipeline.audio import voice_volume  # noqa: E402
from pipeline.config import (  # noqa: E402
    NARRATION_DEESSER,
    NARRATION_LIMITER,
    NARRATION_PRESENCE,
    PACING_SETTINGS,
)
from pipeline.narration import (  # noqa: E402
    narration_chunks,
    split_script,
)

SCRIPT = """The stadium was on its feet. Nobody expected it after the first half.

Ada: We had nothing left in the tank. Ryan: Not true at all!

Then the whistle went, and the whole place collapsed into noise. It was the
kind of finish you talk about for years. Nobody wanted to leave.
"""


def ffmpeg(args, capture=False):
    run = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args],
                         capture_output=True, text=True)
    if run.returncode != 0:
        raise RuntimeError(run.stderr.strip()[:300])
    return run


def synth(text, voice, out, length_scale, noise_scale, noise_w, sentence_silence):
    piper_synth.synthesize(
        text, voice, out,
        length_scale=length_scale,
        noise_scale=noise_scale,
        noise_w_scale=noise_w,
        sentence_silence=sentence_silence,
    )


def silence_file(path, seconds):
    ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
            "-t", str(seconds), "-c:a", "pcm_s16le", path])
    return path


def concat(parts, out):
    # ffmpeg's concat demuxer drops unreadable entries with only a warning and
    # still exits 0, which silently truncates the audio. Refuse to build a
    # listing with a missing part.
    missing = [p for p in parts if not p or not os.path.exists(p)]
    if missing:
        raise RuntimeError(f"concat input missing: {missing}")
    listing = out + ".txt"
    with open(listing, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{p}'\n")
    ffmpeg(["-f", "concat", "-safe", "0", "-i", listing,
            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", out])
    os.remove(listing)


def polish(src, out, extra):
    chain = ("highpass=f=70,lowpass=f=14000,"
             + (f"{NARRATION_PRESENCE},{NARRATION_DEESSER}," if extra else "")
             + "acompressor=threshold=0.08:ratio=2.5:attack=15:release=180:makeup=1")
    raw = out + ".raw.wav"
    ffmpeg(["-i", src, "-af", chain, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", raw])
    measured = voice_volume(raw)
    ffmpeg(["-i", raw, "-af", f"volume={measured:.5f},{NARRATION_LIMITER}",
            "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", out])
    os.remove(raw)


def stats(path):
    w = wave.open(path, "rb")
    n, ch, sr = w.getnframes(), w.getnchannels(), w.getframerate()
    import array
    d = array.array("h")
    d.frombytes(w.readframes(n))
    w.close()
    if ch == 2:
        d = array.array("h", [(d[i] + d[i + 1]) // 2 for i in range(0, len(d), 2)])
    peak = max((abs(x) for x in d), default=0)
    full = sum(1 for x in d if abs(x) >= 32767)
    rms = math.sqrt(sum(x * x for x in d) / len(d)) if d else 0
    return {
        "dur": n / sr,
        "peak_db": 20 * math.log10(peak / 32768) if peak else float("-inf"),
        "rms_db": 20 * math.log10(rms / 32768) if rms else float("-inf"),
        "clipped": full,
    }


def build_variant(name, voice, mode, workdir):
    """mode: 'old' = per-sentence + default prosody, 'new' = chunked + tuned."""
    tmp = os.path.join(workdir, name)
    os.makedirs(tmp, exist_ok=True)
    pacing = PACING_SETTINGS["Normal Sports Story"]
    sp, pp = pacing["sentence_pause"], pacing["paragraph_pause"]

    if mode == "new":
        units = [(text, None) for _v, text, _e in narration_chunks(SCRIPT, {}, "")]
        ls, ns, nw = 1.0, 0.6, 0.75
    else:
        units = [(s, None) for s in split_script(SCRIPT)]
        ls, ns, nw = 1.0, 0.667, 0.8  # Piper's own defaults, as Vidyra left them

    parts = []
    for i, (text, _v) in enumerate(units):
        out = os.path.join(tmp, f"u{i:03d}.wav")
        synth(text, voice, out, ls, ns, nw, sp if mode == "new" else 0.0)
        parts.append(out)

    # old stitched a synthetic sentence pause between every unit; new only puts
    # the longer paragraph pause between chunks (Piper handles the rest).
    gap = silence_file(os.path.join(tmp, "gap.wav"), pp if mode == "new" else sp)
    if mode == "old":
        text = SCRIPT.replace("\r\n", "\n")
        counts = [len([x for x in re.split(r"(?<=[.!?])\s+", p) if x.strip()])
                  for p in re.split(r"\n\s*\n", text)]
        ends, cur = set(), 0
        for c in counts:
            cur += c
            ends.add(cur)
        long_gap = silence_file(os.path.join(tmp, "long.wav"), pp)
        ordered = []
        for i, p in enumerate(parts):
            ordered.append(p)
            if i < len(parts) - 1:
                ordered.append(long_gap if (i + 1) in ends else gap)
    else:
        ordered = []
        for i, p in enumerate(parts):
            ordered.append(p)
            if i < len(parts) - 1:
                ordered.append(gap)

    raw = os.path.join(tmp, "narration_raw.wav")
    concat(ordered, raw)
    final = os.path.join(tmp, f"narration_{name}.wav")
    polish(raw, final, extra=(mode == "new"))
    s = stats(final)
    s["calls"] = len(units)
    s["path"] = final
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="models/piper/en_US-lessac-medium.onnx")
    ap.add_argument("--out", default="")
    ap.add_argument("--listen", action="store_true",
                    help="play both variants in the default player")
    ap.add_argument("--compare", action="store_true",
                    help="copy both variants to clearly named A/B files")
    args = ap.parse_args()

    if not os.path.exists(args.voice):
        sys.exit(f"voice not found: {args.voice}")

    workdir = args.out or tempfile.mkdtemp(prefix="vidyra_voice_")
    os.makedirs(workdir, exist_ok=True)
    print(f"voice : {args.voice}")
    print(f"output: {workdir}\n")

    results = {}
    for mode in ("old", "new"):
        s = build_variant(mode, args.voice, mode, workdir)
        results[mode] = s
        print(f"  {mode:3s}  calls={s['calls']:2d}  dur={s['dur']:6.2f}s  "
              f"peak={s['peak_db']:6.1f}dB  rms={s['rms_db']:6.1f}dB  "
              f"clipped={s['clipped']}")

    o, n = results["old"], results["new"]
    print(f"\n  chunking removed {o['calls'] - n['calls']} synthesis call(s); "
          f"duration {o['dur']:.2f}s -> {n['dur']:.2f}s "
          f"({n['dur'] - o['dur']:+.2f}s)")

    if args.compare:
        # Same script, same model, only the chunking and post-chain differ.
        old_f = os.path.join(workdir, "1_OLD_per_sentence.wav")
        new_f = os.path.join(workdir, "2_NEW_paragraph_chunks.wav")
        shutil.copy2(results["old"]["path"], old_f)
        shutil.copy2(results["new"]["path"], new_f)
        print(f"\n  A/B pair for the same voice:\n    {old_f}\n    {new_f}")

    print("\n  Listen before changing any of the numbers above. Nothing here")
    print("  measures whether the intonation reads as speech or as a machine.")
    if args.listen:
        if hasattr(os, "startfile"):
            for mode in ("old", "new"):
                os.startfile(results[mode]["path"])


if __name__ == "__main__":
    main()
