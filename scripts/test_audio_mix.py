"""Regression tests for the narration + music mix ("music ducking").

Guards two bugs in the same graph:

  1. The mix ended in `loudnorm=I=-16:LRA=7`, which force-compressed the ~21 dB
     voice-over-music range the mix is built around: narration was pushed
     ~5 dB louder while the music bed was pulled ~5 dB down during gaps.
  2. A `loudnorm` *filter* inside `filter_complex` is not reproducible. The bed
     drifted by up to 20 dB between otherwise identical renders, intermittently
     silencing the music mid-sentence.

Levels are therefore static measured gains (audio.measure_lufs) with a peak
limiter on the end; `loudnorm` is used only for analysis.

  A. mix graph structure (unit)
  B. the music bed survives narration silence, repeatably
  C. music still ducks under narration
  D. the bed is level-matched across quiet and hot tracks
  E. no clipping, and the narration-only path renders

Run:  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/test_audio_mix.py
"""

import array
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.audio import (  # noqa: E402
    MIX_LIMITER,
    VOICE_FILTER,
    measure_lufs,
    mix_filter_graph,
    music_bed_volume,
    voice_volume,
)

SR = 48000
SPEECH = ((0.0, 2.0), (4.0, 6.0), (8.0, 10.0))
GAPS = ((2.75, 4.0), (6.75, 8.0), (10.75, 12.0))
SPEECH_WINS = ((0.5, 2.0), (4.5, 6.0), (8.5, 10.0))
TOTAL = 12.0
REPEATS = 5  # the old bug only showed up intermittently

failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


def write_wav(path, gen, total=TOTAL):
    samples = array.array("h")
    for i in range(int(SR * total)):
        samples.append(max(-32767, min(32767, int(gen(i / SR) * 32767))))
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(samples.tobytes())
    w.close()


def rms_db(path, window=0.25):
    """Windowed RMS in dBFS, returned as a list."""
    w = wave.open(path, "rb")
    data = array.array("h")
    data.frombytes(w.readframes(w.getnframes()))
    channels, rate = w.getnchannels(), w.getframerate()
    w.close()
    if channels == 2:
        data = array.array(
            "h", [(data[i] + data[i + 1]) // 2 for i in range(0, len(data), 2)]
        )
    out, step = [], int(rate * window)
    for start in range(0, len(data) - step, step):
        seg = data[start : start + step]
        if not any(seg):
            out.append(-99.0)
            continue
        rms = math.sqrt(sum(x * x for x in seg) / len(seg)) / 32768
        out.append(20 * math.log10(rms))
    return out


def window_avg(levels, spans):
    vals = [
        levels[i]
        for i in range(len(levels))
        if any(a <= i * 0.25 < e for a, e in spans)
    ]
    return sum(vals) / len(vals) if vals else float("nan")


def volumedetect(path):
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True,
        text=True,
    )
    out = {}
    for line in r.stderr.splitlines():
        for key in ("mean_volume", "max_volume"):
            if key in line:
                out[key] = float(line.split(key + ":")[1].strip().split()[0])
    return out


def render(narration, music, out):
    run = subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", narration, "-stream_loop", "-1", "-i", music,
            "-filter_complex", mix_filter_graph(
                VOICE_FILTER, "1:a", 0.35, music_path=music, voice_path=narration
            ),
            "-map", "[out]", "-ar", "48000", "-ac", "2",
            "-c:a", "pcm_s16le", out,
        ],
        capture_output=True,
        text=True,
    )
    return run.returncode == 0, run.stderr.strip()[:140]


def main():
    root = tempfile.mkdtemp(prefix="vidyra_audio_")
    try:
        print("A. mix graph structure")
        graph = mix_filter_graph(VOICE_FILTER, "1:a", 0.35)
        check("no loudnorm anywhere in the mix graph", "loudnorm" not in graph)
        check("mix ends in a peak limiter", MIX_LIMITER in graph)
        check("sidechain ducking retained", "sidechaincompress=" in graph)
        check("amix does not halve the bed", "normalize=0" in graph)
        check("graph ends with [out]", graph.rstrip().endswith("[out]"))
        check("gains are static (no loudnorm in helper output)",
              music_bed_volume(None, 0.35) == 0.35 and voice_volume(None) == 1.0)

        narration = os.path.join(root, "narration.wav")
        write_wav(
            narration,
            lambda t: 0.5 * math.sin(2 * math.pi * 220 * t)
            if any(s <= t < e for s, e in SPEECH)
            else 0.0,
        )
        music = os.path.join(root, "music.wav")
        write_wav(music, lambda t: 0.3 * math.sin(2 * math.pi * 440 * t), 20.0)

        print(f"\nB/C. bed survives narration silence ({REPEATS} repeats)")
        gaps, talks, digests = [], [], set()
        for attempt in range(REPEATS):
            out = os.path.join(root, f"mixed{attempt}.wav")
            ok, err = render(narration, music, out)
            if not ok:
                check("ffmpeg renders the mix", False, err)
                return
            digests.add(os.path.getsize(out))
            levels = rms_db(out)
            gaps.append(window_avg(levels, GAPS))
            talks.append(window_avg(levels, SPEECH_WINS))
        check("ffmpeg renders the mix", True)
        check(
            "render is byte-reproducible",
            len(digests) == 1,
            f"{len(digests)} distinct output sizes over {REPEATS} runs",
        )
        worst_gap = min(gaps)
        check(
            "music bed never drops out in silence",
            worst_gap > -36.0,
            f"worst gap={worst_gap:.1f} dB, mean={sum(gaps)/len(gaps):.1f} dB "
            f"(all runs: {', '.join(f'{g:.1f}' for g in gaps)})",
        )
        check(
            "bed level is stable run-to-run",
            max(gaps) - min(gaps) < 0.5,
            f"spread={max(gaps) - min(gaps):.2f} dB",
        )
        mean_talk = sum(talks) / len(talks)
        mean_gap = sum(gaps) / len(gaps)
        check(
            "voice-over-music separation preserved",
            mean_talk - mean_gap > 15.0,
            f"{mean_talk - mean_gap:.1f} dB",
        )

        print("\nD. bed is level-matched across track loudness")
        levels_seen = []
        for label, amp in (("quiet", 0.02), ("normal", 0.3), ("hot", 0.9)):
            track = os.path.join(root, f"{label}.wav")
            write_wav(track, lambda t, a=amp: a * math.sin(2 * math.pi * 440 * t), 20.0)
            out = os.path.join(root, f"m_{label}.wav")
            ok, err = render(narration, track, out)
            if not ok:
                check(f"{label} track renders", False, err)
                continue
            lv = rms_db(out)
            levels_seen.append(
                (label, measure_lufs(track), window_avg(lv, GAPS))
            )
        for label, src, gap in levels_seen:
            print(f"        {label:7s} source={src:6.1f} LUFS -> bed={gap:6.1f} dB")
        if len(levels_seen) == 3:
            spread = max(g for _, _, g in levels_seen) - min(g for _, _, g in levels_seen)
            check(
                "quiet and hot tracks land at the same bed level",
                spread < 1.0,
                f"spread={spread:.2f} dB",
            )

        print("\nE. levels and narration-only path")
        stats = volumedetect(os.path.join(root, "mixed0.wav"))
        check(
            "no clipping",
            stats.get("max_volume", 0.0) < -0.1,
            f"max={stats.get('max_volume')} dBFS",
        )
        solo = os.path.join(root, "solo.wav")
        run = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-i", narration,
                "-af",
                f"{VOICE_FILTER},volume={voice_volume(narration):.5f},{MIX_LIMITER}",
                "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", solo,
            ],
            capture_output=True,
            text=True,
        )
        check("narration-only path renders", run.returncode == 0, run.stderr.strip()[:140])
        if run.returncode == 0:
            solo_stats = volumedetect(solo)
            check(
                "narration-only does not clip",
                solo_stats.get("max_volume", 0.0) < -0.1,
                f"max={solo_stats.get('max_volume')} dBFS",
            )
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print()
    if failures:
        print(f"FAILED ({len(failures)}): {', '.join(failures)}")
        sys.exit(1)
    print("All audio mix checks passed.")


if __name__ == "__main__":
    main()
