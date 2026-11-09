"""Natural Piper narration — extracted verbatim from reference pipeline."""

from __future__ import annotations

import os
import re
import shutil

from . import piper_synth
from .audio import voice_volume
from .config import (
    NARRATION_DEESSER,
    NARRATION_LIMITER,
    NARRATION_PRESENCE,
    PIPER_NOISE_SCALE,
    PIPER_NOISE_W_SCALE,
    PIPER_VOICE_PROSODY,
)
from .utils import run_cmd

# "Name: line" dialogue prefix (speaker names up to ~30 chars, no sentence enders).
_DIALOGUE_RE = re.compile(r"^\s*([A-Za-z][\w .'\-]{0,30}?):\s+(.+)$")

# Cloned voices travel as "clone:<storagePath>" anywhere a Piper voice name is
# accepted (narrator setting, per-scene voice, per-character voice). One
# convention for frontend, job validation and both workers.
CLONE_VOICE_PREFIX = "clone:"


def clone_ref(voice: str) -> str:
    """Storage path behind a "clone:<path>" value, or "" for Piper voices."""
    if isinstance(voice, str) and voice.startswith(CLONE_VOICE_PREFIX):
        return voice[len(CLONE_VOICE_PREFIX):].strip()
    return ""


def is_clone_voice(voice: str) -> bool:
    return bool(clone_ref(voice))


def clone_lines_for(script: str, voices: dict, default_voice: str = "") -> dict:
    """Chunk texts that must be rendered per cloned voice, for one script.

    Built from ``narration_chunks``, so the keys are exactly the text
    ``create_narration`` will look up in the pre-rendered bank. ``voices`` is
    the speaker -> voice map ("Name:" lines), ``default_voice`` the voice for
    plain narration. Returns {clone_ref_value: [text, ...]} in narration order.
    """
    wanted: dict = {}
    for voice, text, _ends_paragraph in narration_chunks(script, voices, default_voice):
        if text and is_clone_voice(voice):
            wanted.setdefault(voice, []).append(text)
    return wanted


def resolve_speaker(sentence: str, voices: dict):
    """Return (speaker_key, voice_path_or_None, spoken_text) for a sentence.

    ``speaker_key`` is the lowercase character name when the sentence is a
    known character's line ("Ada: hello"), else "". The name is kept separate
    from the voice because two characters may deliberately share one voice, and
    a shared voice must not make their lines run together.
    """
    if not voices:
        return "", None, sentence
    match = _DIALOGUE_RE.match(sentence)
    if not match:
        return "", None, sentence
    speaker = match.group(1).strip().lower()
    spoken = match.group(2).strip()
    if spoken and speaker in voices:
        return speaker, voices[speaker], spoken
    return "", None, sentence


def parse_dialogue(sentence: str, voices: dict):
    """Return (voice_path_or_None, spoken_text) for a sentence.

    If the sentence starts with a known character's name ("Ada: hello"), the
    character's voice is used and the name prefix is stripped from the audio.
    """
    _speaker, voice, spoken = resolve_speaker(sentence, voices)
    return voice, spoken


def split_script(script: str) -> list:
    """Split a script into paragraphs, then sentences."""
    text = script.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    sentences = []
    for paragraph in paragraphs:
        for part in re.split(r"(?<=[.!?])\s+", paragraph):
            part = part.strip()
            if part:
                sentences.append(part)
    return sentences


def narration_chunks(script: str, voices: dict = None, default_voice: str = "") -> list:
    """Group sentences into the units that are actually synthesized.

    Returns ``[(voice, text, ends_paragraph), ...]`` in narration order, where
    ``voice`` is the resolved per-chunk voice ("" = the job's narrator) and
    ``text`` is one or more sentences joined for a single synthesis call.

    Why not one call per sentence: a VITS model draws fresh noise and restarts
    intonation on every call, so a sentence-per-call render resets prosody at
    every period and reads as a sequence of disconnected statements rather than
    a spoken paragraph. Synthesizing a whole paragraph at once lets pitch,
    energy and phrasing carry across sentence boundaries.

    A chunk never spans a paragraph break or a change of speaker, because a
    single synthesis call can only use one voice and because those boundaries
    want the longer pause. This is the single source of truth for chunking:
    ``create_narration`` synthesizes these and ``clone_lines_for`` collects the
    same text, so the pre-rendered clone bank keys always match the lookups.
    """
    chunks: list = []
    current_key = None
    current_voice = None
    current: list = []

    def flush(ends_paragraph: bool):
        nonlocal current
        if current:
            chunks.append((current_voice or "", " ".join(current), ends_paragraph))
            current = []

    for paragraph in _split_paragraphs(script):
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", paragraph) if p.strip()]
        for index, part in enumerate(parts):
            speaker, speaker_voice, spoken = resolve_speaker(part, voices or {})
            voice = speaker_voice or (default_voice or "")
            # A speaker change is a hard boundary even when both characters were
            # given the same voice: they are different people, so the lines need
            # the longer pause rather than running on as one sentence.
            key = (speaker, voice)
            if current and key != current_key:
                flush(False)
            if not current:
                current_key = key
                current_voice = voice
            current.append(spoken)
            if index == len(parts) - 1:
                flush(True)
    flush(False)
    return chunks


def _split_paragraphs(script: str) -> list:
    """Non-empty paragraphs of a script, with line endings normalised."""
    text = (script or "").replace("\r\n", "\n").replace("\r", "\n")
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]



def piper_prosody(voice_path: str) -> tuple:
    """(noise_scale, noise_w_scale) for a voice, honouring per-voice overrides."""
    stem = os.path.splitext(os.path.basename(voice_path or ""))[0]
    override = PIPER_VOICE_PROSODY.get(stem) or {}
    return (
        override.get("noise_scale", PIPER_NOISE_SCALE),
        override.get("noise_w_scale", PIPER_NOISE_W_SCALE),
    )


def synth_chunk(text, voice_path, output_file, speech_rate=1.0, sentence_pause=0.18):
    """Synthesize one chunk in-process, with the shared prosody settings."""
    noise_scale, noise_w_scale = piper_prosody(voice_path)
    return piper_synth.synthesize(
        text,
        voice_path,
        output_file,
        length_scale=1.0 / float(speech_rate),
        noise_scale=noise_scale,
        noise_w_scale=noise_w_scale,
        sentence_silence=float(sentence_pause),
    )


def create_narration(
    settings,
    output_path,
    on_progress=None,
) -> str:
    """Generate polished narration WAV from the script using the Piper CLI.

    One synthesis call per *chunk* (a paragraph, or a run of sentences by one
    speaker) rather than per sentence — see ``narration_chunks`` for why.
    Inter-sentence pauses inside a chunk come from Piper's own
    ``--sentence-silence``; the longer preset pause is only inserted between
    chunks. Pre-cloned lines are copied from the bank instead of synthesized.
    """
    from .config import PACING_SETTINGS

    script = settings.script
    if not script or not script.strip():
        raise ValueError("No script provided.")

    speech_rate = float(settings.speech_rate)
    if not 0.85 <= speech_rate <= 1.15:
        raise ValueError("Narration speed must be between 0.85x and 1.15x.")

    voice = settings.voice_path
    if not voice:
        raise FileNotFoundError("No narrator voice selected.")
    if not is_clone_voice(voice) and not os.path.exists(voice):
        raise FileNotFoundError(f"Piper voice not found: {voice}")

    dialogue_voices = getattr(settings, "dialogue_voices", {}) or {}
    for name, path in dialogue_voices.items():
        if is_clone_voice(path):
            continue
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"Piper voice for '{name}' not found: {path}")

    # Pre-cloned lines: {clone voice value: {chunk text: local wav}}. Copied
    # per call so storyboard scenes can hand out a subset without consuming
    # their siblings' lines.
    clone_banks = {
        key: dict(value)
        for key, value in (getattr(settings, "clone_line_banks", {}) or {}).items()
    }

    pacing = PACING_SETTINGS.get(
        settings.pacing, PACING_SETTINGS["Normal Sports Story"]
    )
    sentence_pause = pacing["sentence_pause"]
    paragraph_pause = pacing["paragraph_pause"]

    temp_dir = settings.temp_dir
    for name in os.listdir(temp_dir):
        if name.startswith(("sentence_", "chunk_")) and name.endswith(".wav"):
            os.remove(os.path.join(temp_dir, name))

    chunks = narration_chunks(script, dialogue_voices, voice)
    if not chunks:
        raise ValueError("No usable sentences found.")

    def report():
        if on_progress:
            on_progress("narration", 0, "")

    report()

    chunk_files = []
    total = len(chunks)

    for index, (chunk_voice, text, _ends_paragraph) in enumerate(chunks):
        output = os.path.join(temp_dir, f"chunk_{index:03d}.wav")
        if is_clone_voice(chunk_voice):
            # Already synthesized in one GPU run for the whole scene/job.
            bank = clone_banks.get(chunk_voice) or {}
            source = bank.get(text)
            if not source or not os.path.exists(source):
                raise FileNotFoundError(
                    f"Cloned line missing from the pre-rendered bank: “{text[:60]}”"
                )
            shutil.copy2(source, output)
        else:
            line_voice = chunk_voice or voice
            synth_chunk(
                text,
                line_voice,
                output,
                speech_rate=speech_rate,
                sentence_pause=sentence_pause,
            )
        chunk_files.append(output)
        if on_progress:
            on_progress(
                "narration",
                int(((index + 1) / total) * 100),
                f"Narrating {index + 1}/{total}",
            )

    # Every chunk ends a paragraph or a speaker change, so every gap between
    # chunks gets the longer preset pause.
    chunk_pause = os.path.join(temp_dir, "chunk_pause.wav")
    run_cmd(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "anullsrc=r=48000:cl=mono",
            "-t", str(paragraph_pause),
            "-c:a", "pcm_s16le",
            chunk_pause,
        ]
    )

    concat_file = os.path.join(temp_dir, "narration_concat.txt")
    # ffmpeg's concat demuxer drops unreadable entries with a warning and still
    # exits 0, so a missing chunk file would silently shorten the narration
    # instead of failing. Refuse to build the listing.
    missing = [p for p in chunk_files if not os.path.exists(p)]
    if missing:
        raise FileNotFoundError(f"Narration chunks missing: {missing}")
    with open(concat_file, "w", encoding="utf-8") as f:
        for index, filename in enumerate(chunk_files):
            f.write(f"file '{os.path.abspath(filename)}'\n")
            if index < len(chunk_files) - 1:
                f.write(f"file '{os.path.abspath(chunk_pause)}'\n")

    raw_narration = os.path.join(temp_dir, "narration_raw.wav")
    run_cmd(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-ar", "48000", "-ac", "1",
            "-c:a", "pcm_s16le",
            raw_narration,
        ]
    )

    # Conditioning, then a *static* measured gain to VOICE_TARGET_DB.
    #
    # This used to end in a `loudnorm` filter, which is not reproducible inside
    # a filter graph -- it moved the narration level between otherwise identical
    # renders. `voice_volume` measures the actual file once and applies one
    # fixed gain, so the polished narration is deterministic. The de-esser and
    # presence lift are static filters, so they keep that property.
    run_cmd(
        [
            "ffmpeg", "-y",
            "-i", raw_narration,
            "-af",
            (
                "highpass=f=70,"
                "lowpass=f=14000,"
                f"{NARRATION_PRESENCE},"
                f"{NARRATION_DEESSER},"
                "acompressor="
                "threshold=0.08:ratio=2.5:attack=15:release=180:makeup=1,"
                f"volume={voice_volume(raw_narration):.5f},"
                f"{NARRATION_LIMITER}"
            ),
            "-ar", "48000", "-ac", "1",
            "-c:a", "pcm_s16le",
            output_path,
        ]
    )

    return output_path