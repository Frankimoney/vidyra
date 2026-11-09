"""Vidyra Gradio dev/testing app.

Preserved from the reference Colab notebook (Cell 21) but now driven by the
reusable `pipeline` package. Kept purely for development / quick testing;
the production interface is the Vidyra web app.
"""

import os
import tempfile

import gradio as gr

from pipeline.config import (
    HOOK_STYLES,
    PACING_SETTINGS,
    CAPTION_STYLES,
    CAPTION_POSITIONS,
    TRANSITIONS,
    RenderSettings,
)
from pipeline.engine import generate_video
from pipeline.voices import find_music_tracks, find_voices

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

VOICE_DIR = os.environ.get("VIDYRA_VOICE_DIR", "models/piper")
MUSIC_DIR = os.environ.get("VIDYRA_MUSIC_DIR", "config/music")
OUTPUT_DIR = os.environ.get("VIDYRA_OUTPUT_DIR", "storage/outputs")

os.makedirs(OUTPUT_DIR, exist_ok=True)

PIPER_VOICES = find_voices(VOICE_DIR)
MUSIC_TRACKS = find_music_tracks(MUSIC_DIR)

if not PIPER_VOICES:
    raise RuntimeError(
        f"No Piper voices found in {VOICE_DIR}. "
        "Download .onnx voices there (or set VIDYRA_VOICE_DIR)."
    )

WHISPER_MODEL = None


def _load_whisper():
    global WHISPER_MODEL
    if WHISPER_MODEL is None:
        print("Loading Whisper base model...")
        import whisper  # type: ignore

        WHISPER_MODEL = whisper.load_model("base")
    return WHISPER_MODEL


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def process(
    script,
    media,
    voice_name,
    speech_rate,
    pacing,
    captions,
    font_size,
    words_per_line,
    caption_position,
    caption_style,
    hook,
    hook_style,
    hook_text,
    hook_subtext,
    hook_duration,
    music_enabled,
    music,
    transition,
):
    music_path = None
    if music_enabled:
        if music:
            music_path = music.get("path") if isinstance(music, dict) else music
        elif MUSIC_TRACKS:
            music_path = list(MUSIC_TRACKS.values())[0]

    media_paths = []
    for item in media or []:
        path = item.get("path") if isinstance(item, dict) else item
        if path:
            media_paths.append(path)

    work_dir = tempfile.mkdtemp(prefix="vidyra_gradio_")
    output_path = os.path.join(OUTPUT_DIR, "vidyra_preview.mp4")

    settings = RenderSettings(
        script=script,
        media_files=media_paths,
        voice_path=PIPER_VOICES[voice_name],
        speech_rate=speech_rate,
        pacing=pacing,
        hook=hook,
        hook_style=hook_style,
        hook_text=hook_text,
        hook_subtext=hook_subtext,
        hook_duration=hook_duration,
        captions=captions,
        caption_style=caption_style,
        font_size=font_size,
        words_per_line=words_per_line,
        caption_position=caption_position,
        music_path=music_path,
        transition=transition,
        temp_dir=work_dir,
        output_path=output_path,
    )

    final = generate_video(
        settings,
        whisper_model=_load_whisper(),
        on_stage=lambda s: print(f"\n>>> STAGE: {s}"),
        on_progress=lambda s, p, m: print(f"    {p:3d}% {m}"),
    )
    return final


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

with gr.Blocks(title="Vidyra — Dev Preview") as app:
    gr.Markdown(
        """
        # ⚡ VIDYRA — DEV PREVIEW
        Create a vertical 9:16 sports video. *(Gradio is development-only.)*
        """
    )

    script = gr.Textbox(
        label="SCRIPT",
        lines=8,
        placeholder="Paste your finished sports script here...",
    )

    media = gr.File(
        label="MEDIA — Images or ONE Video",
        file_count="multiple",
        type="filepath",
    )

    with gr.Row():
        voice = gr.Dropdown(
            choices=list(PIPER_VOICES.keys()),
            value=list(PIPER_VOICES.keys())[0],
            label="NARRATOR — Voice",
        )
        speech_rate = gr.Slider(
            minimum=0.85, maximum=1.15, value=1.00, step=0.01, label="SPEED"
        )

    pacing = gr.Dropdown(
        choices=list(PACING_SETTINGS.keys()),
        value="Normal Sports Story",
        label="PACING",
    )

    with gr.Row():
        captions = gr.Checkbox(value=True, label="CAPTIONS")
        hook = gr.Checkbox(value=True, label="HOOK")
        music_enabled = gr.Checkbox(value=True, label="MUSIC")

    with gr.Row():
        font_size = gr.Slider(
            minimum=36, maximum=90, value=58, step=2, label="Caption Font Size"
        )
        words_per_line = gr.Slider(
            minimum=2, maximum=8, value=5, step=1, label="Words Per Line"
        )

    with gr.Row():
        caption_position = gr.Dropdown(
            choices=list(CAPTION_POSITIONS.keys()),
            value="Bottom",
            label="Caption Position",
        )
        caption_style = gr.Dropdown(
            choices=list(CAPTION_STYLES.keys()),
            value="Karaoke",
            label="Caption Style",
        )

    hook_style = gr.Dropdown(
        choices=HOOK_STYLES, value="Breaking News", label="HOOK STYLE"
    )
    hook_text = gr.Textbox(value="BROOKE QUARLES DANIELS", label="Hook Main Text")
    hook_subtext = gr.Textbox(value="IS PUTTING IN THE WORK", label="Hook Subtext")
    hook_duration = gr.Slider(
        minimum=0.5, maximum=3.0, value=1.5, step=0.1, label="Hook Duration"
    )

    music = gr.File(label="Background Music (optional — else first catalog track)")

    transition = gr.Dropdown(
        choices=TRANSITIONS, value="Auto", label="TRANSITION"
    )

    generate = gr.Button("⚡ GENERATE VIDEO", variant="primary")
    output = gr.Video(label="FINAL VIDEO")

    generate.click(
        fn=process,
        inputs=[
            script, media, voice, speech_rate, pacing,
            captions, font_size, words_per_line, caption_position, caption_style,
            hook, hook_style, hook_text, hook_subtext, hook_duration,
            music_enabled, music, transition,
        ],
        outputs=output,
    )

if __name__ == "__main__":
    app.launch(server_name="0.0.0.0", server_port=7860)