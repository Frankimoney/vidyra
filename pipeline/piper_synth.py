"""In-process Piper synthesis with the voice model kept loaded.

Replaces the per-chunk `piper` command line invocation for speed: the CLI
reloads and re-warms the ONNX model on every call, and `create_narration` used
to call it once per sentence. Loaded once per process, the same render skips
that entirely.

**On reproducibility -- measured, not assumed.** Vidyra's audio work requires
reproducible output, so this is worth stating precisely, because the obvious
fix does not work:

VITS takes its generator noise as an input `scales` value, and the exported
ONNX graph injects the noise itself with a `RandomNormalLike` op. The random
number generator is therefore ONNX Runtime's, not torch's, so seeding torch
cannot reach it. Measured on this host, same text, same settings:

    noise 0.667 / 0.8 (Piper defaults)  3 runs -> 3 different WAVs
    noise 0.6 / 0.75 (ours)             3 runs -> 3 different WAVs
    noise 0.0 / 0.0                     3 runs -> 1 identical WAV
    noise 0.0 / 0.0, OMP_NUM_THREADS=1  identical to the above

So the trade-off is exact and unavoidable: expressive or reproducible, not
both. `noise=0` is the flatter, more uniform delivery that is the very
"robotic" quality this work exists to remove, so the default keeps noise on
and TTS output is *not* byte-reproducible. Everything downstream of the
synthesizer -- pause placement, the static measured gain, de-esser, presence
lift, limiter, and the music mix -- is deterministic given a given narration
WAV, which is the part that was actually broken before and is now covered by
scripts/test_audio_mix.py.

Pass ``deterministic=True`` for a reproducible render when that matters more
than expression (for example diffing two renders); it forces both noise scales
to zero.
"""

import os
import threading
import wave

_LOCK = threading.Lock()
_VOICES = {}

# torch and piper are imported lazily inside the functions so a host without
# the piper wheels -- the RunPod worker, which never synthesizes locally -- can
# still import this module.


def _load(voice_path):
    """Load and cache a PiperVoice per model path."""
    key = os.path.abspath(voice_path)
    with _LOCK:
        voice = _VOICES.get(key)
        if voice is None:
            from piper import PiperVoice

            voice = PiperVoice.load(key, config_path=key + ".json", use_cuda=False)
            _VOICES[key] = voice
    return voice


def synthesize(
    text,
    voice_path,
    output_file,
    length_scale=1.0,
    noise_scale=0.6,
    noise_w_scale=0.75,
    sentence_silence=0.18,
    volume=1.0,
    deterministic=False,
):
    """Synthesize `text` to a WAV file. Returns output_file.

    Piper emits one audio chunk per sentence inside `text`; they are joined
    here with `sentence_silence` of digital silence, which is what lets a whole
    paragraph be a single call while still sounding like punctuated speech.
    Mirrors `piper/__main__.py`: same SynthesisConfig fields, same silence
    bytes, same WAV framing.
    """
    from piper import SynthesisConfig

    voice = _load(voice_path)

    if deterministic:
        # The only way to silence the graph's RandomNormalLike, at the cost of
        # the prosodic variation that makes the voice sound spoken.
        noise_scale = 0.0
        noise_w_scale = 0.0

    syn_config = SynthesisConfig(
        length_scale=float(length_scale),
        noise_scale=float(noise_scale),
        noise_w_scale=float(noise_w_scale),
        normalize_audio=True,
        volume=float(volume),
    )

    # 16-bit mono silence of the requested length, as the CLI builds it.
    silence = bytes(int(voice.config.sample_rate * float(sentence_silence) * 2))

    framed = False
    with wave.open(output_file, "wb") as wav:
        for index, chunk in enumerate(voice.synthesize(text, syn_config)):
            if not framed:
                wav.setframerate(chunk.sample_rate)
                wav.setsampwidth(chunk.sample_width)
                wav.setnchannels(chunk.sample_channels)
                framed = True
            if index > 0:
                wav.writeframes(silence)
            wav.writeframes(chunk.audio_int16_bytes)

    return output_file
