"""Karaoke-style ASS captions via Whisper word timestamps — reference logic."""

from __future__ import annotations

import os

from .utils import run_cmd


def ass_time(seconds):
    """Format seconds as ASS timestamp H:MM:SS.cc"""
    seconds = max(0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - hours * 3600 - minutes * 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _clean_word(word):
    return word.replace("{", "").replace("}", "").replace("\\", "")


def transcribe_words(whisper_model, narration_audio):
    """Return [{word, start, end}, ...] via Whisper word timestamps."""
    result = whisper_model.transcribe(
        narration_audio,
        language="en",
        word_timestamps=True,
        fp16=False,
    )
    words = []
    for segment in result.get("segments", []):
        for word in segment.get("words", []):
            text = word.get("word", "").strip()
            if text:
                words.append(
                    {
                        "word": text,
                        "start": float(word["start"]),
                        "end": float(word["end"]),
                    }
                )
    if not words:
        raise RuntimeError("Whisper returned no word timestamps.")
    return words


def shift_ass(input_path, output_path, offset_seconds):
    """Shift every Dialogue event by a fixed offset (used for hook sync)."""
    with open(input_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    shifted = []
    for line in lines:
        if not line.startswith("Dialogue:"):
            shifted.append(line)
            continue

        parts = line.rstrip("\n").split(",", 9)
        if len(parts) < 10:
            shifted.append(line)
            continue

        parts[1] = shift_ass_time(parts[1], offset_seconds)
        parts[2] = shift_ass_time(parts[2], offset_seconds)
        shifted.append(",".join(parts) + "\n")

    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(shifted)

    return output_path


def shift_ass_time(time_string, offset):
    hours, minutes, seconds = time_string.split(":")
    total = int(hours) * 3600 + int(minutes) * 60 + float(seconds) + offset
    return ass_time(total)


def create_captions(narration, whisper_model, settings):
    """Generate karaoke ASS captions (optionally synced with hook offset)."""
    import whisper  # type: ignore  (lazy: heavy import)

    cap = settings.caption_values()
    words = transcribe_words(whisper_model, narration)

    font_name = "DejaVu Sans"
    if settings.font_file:
        # Prefer a real font family name when available; fall back to default.
        font_name = "DejaVu Sans"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {settings.width}
PlayResY: {settings.height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{cap['font_size']},{cap['primary']},{cap['secondary']},&H80000000,&H80000000,{cap['bold']},0,0,0,100,100,0,0,1,4,1,5,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    # Clamped here as well as at the settings boundary: `range(0, n, 0)` raises,
    # and a bad saved value would take the whole render down after every GPU run
    # has already been spent.
    try:
        words_per_line = max(1, int(cap["words_per_line"]))
    except (TypeError, ValueError):
        words_per_line = 5
    center_x = settings.width // 2
    y_position = cap["position"]

    for start_index in range(0, len(words), words_per_line):
        group = words[start_index : start_index + words_per_line]
        if not group:
            continue

        for active_index, active in enumerate(group):
            start_time = active["start"]
            if active_index + 1 < len(group):
                end_time = group[active_index + 1]["start"]
            else:
                end_time = group[-1]["end"]

            display_words = []
            for index, item in enumerate(group):
                word = _clean_word(item["word"])
                if settings.caption_style == "Karaoke" and index == active_index:
                    display_words.append(
                        r"{\c&H00FFFF&}" + word + r"{\c&HFFFFFF&}"
                    )
                else:
                    display_words.append(word)

            caption = " ".join(display_words)
            events.append(
                "Dialogue: 0,"
                f"{ass_time(start_time)},"
                f"{ass_time(end_time)},"
                "Default,,0,0,0,,"
                f"{{\\pos({center_x},{y_position})}}"
                f"{caption}"
            )

    ass_file = os.path.join(settings.temp_dir, "captions.ass")
    with open(ass_file, "w", encoding="utf-8") as f:
        f.write(header)
        f.write("\n".join(events))

    if settings.hook:
        synced = os.path.join(settings.temp_dir, "captions_synced.ass")
        shift_ass(ass_file, synced, settings.hook_duration)
        return synced

    return ass_file