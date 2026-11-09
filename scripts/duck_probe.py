import os
import subprocess
import sys
import wave

import numpy as np

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _ROOT)
from pipeline.audio import VOICE_FILTER
from pipeline.config import (
    MUSIC_DUCK_ATTACK as A,
    MUSIC_DUCK_RATIO as R,
    MUSIC_DUCK_RELEASE as REL,
    MUSIC_DUCK_THRESHOLD as T,
)

NAR = os.path.join(_ROOT, "storage", "voice_ab", "listen", "3_female_medium_hfc.wav")
MUS = os.path.join(_ROOT, "config", "music", "ambient-calm.mp3")
OUT = "C:/Users/NWAJI/AppData/Local/Temp/opencode/ducknew"
os.makedirs(OUT, exist_ok=True)


DURATION = float(wave.open(NAR).getnframes()) / wave.open(NAR).getframerate()


def run(graph, out):
    # `-stream_loop -1` never ends on its own, so the length has to be bounded.
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", NAR, "-stream_loop", "-1", "-i", MUS,
         "-filter_complex", graph, "-map", "[out]", "-ar", "48000",
         "-t", f"{DURATION:.3f}", out],
        capture_output=True,
    )
    if r.returncode:
        print("FFMPEG FAIL", out)
        print(r.stderr.decode()[-400:])
    return r.returncode == 0


ok_ref = run("[1:a]volume=0.3[out]", os.path.join(OUT, "ref.wav"))
graph = (
    f"[0:a]{VOICE_FILTER}[voice];"
    "[1:a]volume=0.3[music];"
    f"[music][voice]sidechaincompress=threshold={T}:ratio={R}:attack={A}:release={REL}[out]"
)
ok_new = run(graph, os.path.join(OUT, "new.wav"))
print("render ref:", ok_ref, "render ducked:", ok_new)
print("params threshold=%.3f ratio=%.1f attack=%dms release=%dms" % (T, R, A, REL))


def load(path):
    w = wave.open(path)
    sr = w.getframerate()
    ch = w.getnchannels()
    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float64) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


v, sr = load(NAR)
ref, _ = load(os.path.join(OUT, "ref.wav"))
ducked, _ = load(os.path.join(OUT, "new.wav"))
n = min(len(v), len(ref), len(ducked))
v, ref, ducked = v[:n], ref[:n], ducked[:n]


def db(x):
    return 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12)


print()
print("  t(s)   voice   bed_ref   bed_duck   duck_dB   state")
step = 0.4
speech_bed = []
quiet_bed = []
for i in range(int(len(v) / sr / step)):
    a, b = int(i * step * sr), int((i + 1) * step * sr)
    if b > len(v):
        break
    vv, rr, dd = db(v[a:b]), db(ref[a:b]), db(ducked[a:b])
    state = "speech" if vv > -45 else "quiet"
    (speech_bed if state == "speech" else quiet_bed).append(dd)
    print("%6.2f %7.1f %9.1f %10.1f %8.1f   %s" % (i * step, vv, rr, dd, rr - dd, state))

if speech_bed:
    import statistics as st
    print()
    print("bed during speech: %.2f dBFS" % st.mean(speech_bed))
    if quiet_bed:
        print("bed during quiet : %.2f dBFS" % st.mean(quiet_bed))
        print("duck depth       : %.2f dB" % (st.mean(quiet_bed) - st.mean(speech_bed)))
    else:
        print("no window quiet enough to measure recovery")
