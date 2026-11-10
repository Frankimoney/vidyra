"""Background video-generation worker.

Jobs are processed sequentially (configurable concurrency) on a thread
executor so the asyncio event loop stays responsive. Progress is written
back to Firestore as the engine reports it.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
import traceback
import datetime as dt

from pipeline import aspect as aspect_lib
from pipeline.audio import clamp_music_volume
from pipeline.config import VIDEO_HEIGHT, VIDEO_WIDTH, RenderSettings, bounded_float, bounded_int
from pipeline.engine import generate_video
from pipeline.narration import (
    CLONE_VOICE_PREFIX,
    clone_lines_for,
    clone_ref,
    is_clone_voice,
)
from pipeline.utils import PipelineError, classify_media, get_duration, run_cmd
from pipeline.voices import find_music_tracks, find_voices

from ..config import Settings
from ..services import jobs as jobs_service
from ..services.storage.local import as_absolute


# Cloned voices travel as "clone:<storagePath>" anywhere a Piper voice name is
# accepted (narrator, per-scene voice, per-character voice). The prefix and
# helpers live in pipeline.narration so the renderer and both workers agree.
CLONE_VOICE_PREFIX = CLONE_VOICE_PREFIX

class VideoWorker:
    def __init__(self, settings: Settings, storage):
        self.settings = settings
        self.storage = storage
        self._semaphore = asyncio.Semaphore(max(1, settings.max_concurrency))
        self._whisper_model = None
        self._whisper_lock = threading.Lock()
        # Injected in main.lifespan (see app.state.worker.studio_worker).
        self.studio_worker = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def submit(self, job_id: str) -> None:
        task = asyncio.create_task(self._run(job_id))
        task.add_done_callback(self._on_done)

    def _on_done(self, task):
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            # Last-resort error handler (individual jobs handle their own errors).
            pass

    async def _run(self, job_id: str) -> None:
        async with self._semaphore:
            await asyncio.to_thread(self._process, job_id)

    # ------------------------------------------------------------------
    # Sync processing (runs on thread)
    # ------------------------------------------------------------------

    def _process(self, job_id: str) -> None:
        work_dir = os.path.join(self.settings.work_root, job_id)
        try:
            os.makedirs(work_dir, exist_ok=True)

            job = self._load_job(job_id)

            if job.get("type") == "face":
                self._process_face(job_id, job, work_dir)
                return

            if job.get("scenes"):
                self._process_storyboard(job_id, job, work_dir)
                return

            self._begin(job_id)

            media_local, clip_durations, trim_starts, clip_focus, clip_zoom = self._fetch_media(
                job_id, job
            )

            cover_local = self._resolve_cover(job, media_local)

            uid = job.get("userId", "unknown")
            clone_ref_value = self.clone_ref_from_voice((job.get("settings") or {}).get("voice"))
            clone_wav = ""
            if clone_ref_value and not self.settings.runpod_fake:
                # One continuous pass in the cloned voice (no sentence split),
                # reusing the same run path as multi-voice jobs.
                voice_value = CLONE_VOICE_PREFIX + clone_ref_value
                clone_wav, _ = self._clone_voice(
                    job_id, uid, voice_value, [(job.get("script") or "").strip()], work_dir, "Narration"
                )
                voice_path = ""
            else:
                voice_path = self._resolve_voice(job.get("settings", {}).get("voice"))
            music_path = self._resolve_music(job.get("settings", {}).get("music"))

            settings = self._build_settings(
                job, media_local, clip_durations, cover_local, voice_path, music_path, work_dir,
                trim_starts, clip_focus, clip_zoom,
            )
            if clone_wav:
                settings.voice_clone_wav = clone_wav

            def report(stage, progress, message):
                jobs_service.update_job(
                    job_id,
                    status="processing",
                    stage=stage,
                    progress=progress,
                    message=message or stage,
                )

            whisper = None

            output_path = generate_video(
                settings,
                whisper_model=whisper,
                on_progress=report,
                on_stage=lambda s: report(s, 0, s),
            )

            self._publish(job_id, job, output_path, settings)
        except Exception as exc:
            self._fail(job_id, exc)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Multi-scene storyboard (job has "scenes")
    # ------------------------------------------------------------------

    def _process_storyboard(self, job_id: str, job: dict, work_dir: str) -> None:
        from pipeline.storyboard import generate_storyboard

        self._begin(job_id)
        s = job.get("settings") or {}

        dialogue_voices: dict = {}
        for char in job.get("characters") or []:
            name = (char.get("name") or "").strip().lower()
            voice = char.get("voice")
            if name and voice:
                dialogue_voices[name] = self._voice_or_clone(voice)

        media_root = os.path.join(work_dir, "media")
        os.makedirs(media_root, exist_ok=True)

        scenes = []
        all_media: list = []
        for si, scene in enumerate(job.get("scenes") or []):
            paths, durations, trims, foci, zooms = [], [], [], [], []
            speeds, reverses, freezes, motions, keyframes = [], [], [], [], []
            for mi, item in enumerate(scene.get("media") or []):
                if isinstance(item, str):
                    storage_path, duration, trim = item, None, 0
                    focus, zoom = (0.5, 0.5), 1.0
                    speed, reverse, freeze, motion, kfs = 1.0, False, False, "auto", []
                else:
                    storage_path = item.get("path", "")
                    duration = item.get("duration")
                    trim = item.get("trimStart", 0)
                    focus = (item.get("focusX", 0.5), item.get("focusY", 0.5))
                    zoom = item.get("zoom", 1.0)
                    speed = item.get("speed", 1.0)
                    reverse = bool(item.get("reverse"))
                    freeze = bool(item.get("freeze"))
                    motion = item.get("motion", "auto")
                    kfs = item.get("keyframes") or []
                if not storage_path:
                    continue
                ext = os.path.splitext(storage_path)[1].lower()
                if classify_media(storage_path) is None:
                    raise PipelineError(f"Unsupported media type: {ext or storage_path}")
                local = os.path.join(media_root, f"s{si:02d}_m{mi:02d}{ext}")
                paths.append(self.storage.resolve(storage_path, local))
                durations.append(
                    float(duration)
                    if isinstance(duration, (int, float)) and duration > 0
                    else None
                )
                trims.append(
                    max(0.0, float(trim)) if isinstance(trim, (int, float)) and trim > 0 else 0
                )
                fx, fy = focus if isinstance(focus, (tuple, list)) and len(focus) == 2 else (0.5, 0.5)
                try:
                    fx = max(0.0, min(1.0, float(fx)))
                    fy = max(0.0, min(1.0, float(fy)))
                except (TypeError, ValueError):
                    fx, fy = 0.5, 0.5
                foci.append((fx, fy))
                try:
                    zooms.append(max(1.0, min(3.0, float(zoom))))
                except (TypeError, ValueError):
                    zooms.append(1.0)
                try:
                    speeds.append(max(0.25, min(4.0, float(speed))))
                except (TypeError, ValueError):
                    speeds.append(1.0)
                reverses.append(reverse)
                freezes.append(freeze)
                motions.append(str(motion or "auto"))
                clean_kfs = []
                for kf in (kfs if isinstance(kfs, list) else []):
                    if not isinstance(kf, dict):
                        continue
                    try:
                        clean_kfs.append({
                            "t": max(0.0, min(1.0, float(kf.get("t", 0)))),
                            "zoom": max(1.0, min(3.0, float(kf.get("zoom", 1.0)))),
                            "focusX": max(0.0, min(1.0, float(kf.get("focusX", 0.5)))),
                            "focusY": max(0.0, min(1.0, float(kf.get("focusY", 0.5)))),
                        })
                    except (TypeError, ValueError):
                        continue
                keyframes.append(clean_kfs)
            if not paths:
                raise PipelineError(f"Scene {si + 1} has no media.")
            all_media.extend(paths)
            entry = {
                "script": scene.get("script", ""),
                "voice_path": self._voice_or_clone(scene.get("voice") or s.get("voice")),
                "media_files": paths,
                "clip_durations": durations,
                "trim_starts": trims,
                "clip_focus": foci,
                "clip_zoom": zooms,
                "clip_speeds": speeds,
                "clip_reverse": reverses,
                "clip_freeze": freezes,
                "clip_motions": motions,
                "clip_keyframes": keyframes,
                "transition": scene.get("transition"),
                "dialogue_voices": dialogue_voices,
            }
            try:
                scene_duration = float(scene.get("duration") or 0)
            except (TypeError, ValueError):
                scene_duration = 0.0
            if scene_duration > 0:
                entry["duration"] = scene_duration
            scenes.append(entry)

        cover_local = self._resolve_cover(job, all_media)
        music_path = self._resolve_music(s.get("music"))

        settings = self._build_settings(
            job, all_media, [], cover_local, scenes[0]["voice_path"], music_path, work_dir
        )
        # One GPU run per cloned voice for the whole job, split per line; the
        # storyboard engine hands each scene only the lines it speaks.
        settings.clone_line_banks = self._clone_line_banks(
            job_id,
            job.get("userId", "unknown"),
            [(sc["script"], sc["dialogue_voices"], sc["voice_path"]) for sc in scenes],
            work_dir,
            label="Storyboard narration",
        )

        def report(stage, progress, message):
            jobs_service.update_job(
                job_id,
                status="processing",
                stage=stage,
                progress=progress,
                message=message or stage,
            )

        whisper = None

        output_path = generate_storyboard(
            scenes,
            settings,
            whisper_model=whisper,
            on_progress=report,
            on_stage=lambda st: report(st, 0, st),
        )

        self._publish(job_id, job, output_path, settings)

    # ------------------------------------------------------------------
    # Face-recording composite (type == "face")
    # ------------------------------------------------------------------

    def _process_face(self, job_id: str, job: dict, work_dir: str) -> None:
        from pipeline.face import compose_face_overlay, probe_dimensions

        jobs_service.update_job(
            job_id,
            status="processing",
            stage="preparing",
            progress=5,
            message="Preparing your face recording",
        )

        parent = jobs_service.get_job(job.get("parentJobId") or "")
        if not parent or parent.get("userId") != job.get("userId"):
            raise PipelineError("Original video not found.")
        out = parent.get("output") or {}
        src = out.get("storagePath")
        if not src:
            raise PipelineError("Original video has no finished output.")

        main_local = self.storage.resolve(src)
        rec_local = self.storage.resolve(job.get("recordingPath") or "")
        if not os.path.isfile(main_local) or not os.path.isfile(rec_local):
            raise PipelineError("Could not read the video or recording.")

        jobs_service.update_job(
            job_id,
            status="processing",
            stage="processing",
            progress=45,
            message="Applying your face recording",
        )

        from pipeline.utils import get_fps

        width, height = probe_dimensions(main_local)
        width = width or 1080
        height = height or 1920
        fps = get_fps(main_local) or 30

        temp_dir = os.path.join(work_dir, "tmp")
        os.makedirs(temp_dir, exist_ok=True)

        composed = compose_face_overlay(
            main_local,
            rec_local,
            job.get("faceOverlay") or {},
            bool(job.get("faceAudio")),
            temp_dir,
            width=width,
            height=height,
            fps=fps,
        )

        jobs_service.update_job(
            job_id,
            status="processing",
            stage="rendering",
            progress=80,
            message="Rendering final video",
        )

        self._publish(job_id, job, composed, settings=None)

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def _load_job(self, job_id: str) -> dict:
        job = jobs_service.get_job(job_id)
        if not job:
            raise PipelineError(f"Job not found: {job_id}")
        return job

    def _begin(self, job_id):
        jobs_service.update_job(
            job_id,
            status="processing",
            stage="preparing",
            progress=0,
            message="Preparing files",
        )

    def _fetch_media(self, job_id: str, job: dict) -> tuple[list, list, list, list, list]:
        paths, durations, trims, foci, zooms = [], [], [], [], []
        for i, item in enumerate(job.get("media") or []):
            if isinstance(item, str):
                storage_path, duration, trim = item, None, 0
                focus, zoom = (0.5, 0.5), 1.0
            else:
                storage_path = item.get("path", "")
                duration = item.get("duration")
                trim = item.get("trimStart", 0)
                focus = (
                    item.get("focusX", 0.5),
                    item.get("focusY", 0.5),
                )
                zoom = item.get("zoom", 1.0)
            if not storage_path:
                continue
            ext = os.path.splitext(storage_path)[1].lower()
            if classify_media(storage_path) is None:
                raise PipelineError(f"Unsupported media type: {ext or storage_path}")
            local = os.path.join(self.settings.work_root, job_id, f"media_{i:02d}{ext}")
            paths.append(self.storage.resolve(storage_path, local))
            durations.append(
                float(duration) if isinstance(duration, (int, float)) and duration > 0 else None
            )
            trims.append(
                max(0.0, float(trim)) if isinstance(trim, (int, float)) and trim > 0 else 0
            )
            fx, fy = focus if isinstance(focus, (tuple, list)) and len(focus) == 2 else (0.5, 0.5)
            try:
                fx = max(0.0, min(1.0, float(fx)))
                fy = max(0.0, min(1.0, float(fy)))
            except (TypeError, ValueError):
                fx, fy = 0.5, 0.5
            foci.append((fx, fy))
            try:
                zooms.append(max(1.0, min(3.0, float(zoom))))
            except (TypeError, ValueError):
                zooms.append(1.0)
        if not paths:
            raise PipelineError("No media files provided.")
        return paths, durations, trims, foci, zooms

    def _resolve_cover(self, job: dict, media_local: list) -> str:
        """Local path for the hook cover, falling back to the first image clip."""
        cover = (job.get("cover") or "").strip()
        if cover:
            try:
                return self.storage.resolve(cover)
            except Exception:
                pass
        for path in media_local:
            if classify_media(path) == "image":
                return path
        return ""

    def _resolve_voice(self, name) -> str:
        if is_clone_voice(name) and not self.settings.runpod_fake:
            return ""  # cloned narration is synthesized by _clone_line_banks
        voices = find_voices(self.settings.voice_dir)
        if name in voices:
            return voices[name]
        # Fall back to the first available voice
        if voices:
            return sorted(voices.values())[0]
        raise PipelineError("No Piper voices available on the server.")

    def studio_quota_remaining(self, uid: str) -> int:
        from ..services import studio as studio_service

        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        used = sum(
            1
            for r in studio_service.list_runs(uid, limit=200)
            if (r.get("createdAt") or "").startswith(today)
        )
        return max(0, int(self.settings.studio_max_runs_per_day) - used)

    def _voice_or_clone(self, name) -> str:
        """Piper model path, or the "clone:<path>" value when a clone is picked."""
        if is_clone_voice(name) and not self.settings.runpod_fake:
            return name
        return self._resolve_voice(name)

    def _clone_voice(
        self, job_id: str, uid: str, voice_value: str, texts, work_dir: str, label: str
    ):
        """Speak every line in one cloned voice in a single GPU run.

        Returns (local track path, per-line durations). Reuses the Studio queue
        (studio_service.create_run + StudioWorker._process) like film portraits,
        so there is one code path to the pod.
        """
        from ..services import studio as studio_service

        ref = clone_ref(voice_value)
        if self.studio_worker is None or not self.settings.studio_enabled:
            raise PipelineError(
                "Cloned voices need AI Studio (RUNPOD_API_KEY + RUNPOD_ENDPOINT_ID)."
            )
        if self.studio_quota_remaining(uid) < 1:
            raise PipelineError(
                "Daily AI Studio quota reached — cloned voices need 1 GPU run per voice."
            )
        self._check_clone_ref(uid, ref)

        jobs_service.update_job(
            job_id,
            stage="narration",
            progress=2,
            message=f"Cloning {label.lower()} in {len(texts)} line(s)",
        )
        run_id = studio_service.create_run(
            uid=uid,
            tool="voiceclone",
            model="chatterbox-turbo",
            prompt="\n".join(texts)[:2000],
            params={"lines": list(texts)},
            refs=[{"path": ref, "role": "audio"}],
            title=f"{label} · cloned voice",
        )
        self.studio_worker._process(run_id)
        doc = studio_service.get_run(run_id) or {}
        if (doc.get("status") or "") != "completed":
            raise PipelineError(
                f"Voice clone failed: {doc.get('error') or doc.get('message') or 'unknown error'}"
            )
        output = doc.get("output") or {}
        primary = (output.get("primary") or {}).get("storagePath")
        if not primary:
            raise PipelineError("Voice clone finished without audio.")
        track = self.storage.resolve(primary, os.path.join(work_dir, "clone_track.wav"))
        return track, ((output.get("meta") or {}).get("lines")) or []

    def _clone_line_banks(
        self,
        job_id: str,
        uid: str,
        segments,
        work_dir: str,
        label: str = "Narration",
    ) -> dict:
        """Pre-clone every spoken line, one GPU run per cloned voice.

        ``segments`` is [(script, dialogue_voices, default_voice), ...] — the
        same triples the renderer uses, so the collected texts match exactly
        what narration looks up. Returns
        {"clone:<path>": {spoken text: local wav}}. Fake mode returns {} so the
        renderer falls back to Piper instead of speaking a test tone.
        """
        wanted: dict = {}
        for script, voices, default_voice in segments:
            lines = clone_lines_for(script, voices or {}, default_voice or "")
            for voice_value, texts in lines.items():
                bucket = wanted.setdefault(voice_value, [])
                for text in texts:
                    if text not in bucket:
                        bucket.append(text)
        if not wanted or self.settings.runpod_fake or not self.settings.studio_enabled:
            return {}

        banks: dict = {}
        for voice_value, texts in wanted.items():
            track, durations = self._clone_voice(
                job_id, uid, voice_value, texts, work_dir, label
            )
            if len(texts) == 1:
                banks[voice_value] = {texts[0]: track}
                continue
            if len(durations) != len(texts):
                raise PipelineError(
                    "Cloned voice came back without per-line timings. "
                    "Rebuild the RunPod image (chatterbox-tts multi-line support)."
                )
            banks[voice_value] = self._split_clone_track(
                track, texts, durations, os.path.join(work_dir, "clone_lines")
            )
        return banks

    def _split_clone_track(self, track: str, texts, durations, out_dir: str) -> dict:
        """Cut one concatenated clone track into per-line WAVs by duration."""
        os.makedirs(out_dir, exist_ok=True)
        out: dict = {}
        offset = 0.0
        for index, (text, length) in enumerate(zip(texts, durations)):
            target = os.path.join(out_dir, f"line_{index:03d}.wav")
            run_cmd(
                [
                    "ffmpeg", "-y",
                    "-ss", f"{offset:.3f}",
                    "-i", track,
                    "-t", f"{max(0.05, float(length)):.3f}",
                    "-ar", "48000", "-ac", "1",
                    "-c:a", "pcm_s16le",
                    target,
                ]
            )
            out[text] = target
            offset += float(length)
        return out

    def _join_wavs(self, files, output: str) -> str:
        """Concatenate same-format WAVs (used to rebuild a multi-sentence turn)."""
        list_file = os.path.join(os.path.dirname(output) or ".", "wav_join.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for path in files:
                f.write(f"file '{os.path.abspath(path)}'\n")
        run_cmd(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", list_file,
                "-ar", "48000", "-ac", "1",
                "-c:a", "pcm_s16le",
                output,
            ]
        )
        return output

    def _check_clone_ref(self, uid: str, ref: str) -> None:
        if not ref or not ref.startswith((f"media/{uid}/", f"studio/{uid}/", f"videos/{uid}/")):
            raise PipelineError("Cloned voice does not belong to you.")
        if classify_media(ref) != "audio":
            raise PipelineError("Cloned voice must be an audio file.")

    @staticmethod
    def clone_ref_from_voice(name) -> str:
        return clone_ref(name)

    def _resolve_music(self, name) -> str | None:
        if not name:
            return None
        # User-uploaded tracks are referenced by their storage path.
        if str(name).startswith("media/"):
            try:
                return self.storage.resolve(str(name))
            except Exception:
                raise PipelineError(f"Music track not found: {name}")
        tracks = find_music_tracks(self.settings.music_dir)
        if name in tracks:
            return tracks[name]
        raise PipelineError(f"Music track not found: {name}")

    def _resolve_timeline(self, job, work_dir):
        """Stage creator timeline tracks under the job work dir.

        Only owners of these storage paths can reach this point (routes check),
        but the worker still resolves through the storage driver so local and
        Firebase backends behave the same.
        """
        timeline = job.get("timeline") or {}
        root = os.path.join(work_dir, "timeline")
        os.makedirs(root, exist_ok=True)
        texts = [
            dict(t) for t in (timeline.get("texts") or [])
            if isinstance(t, dict) and str(t.get("text") or "").strip()
        ]
        overlays = []
        for i, item in enumerate(timeline.get("overlays") or []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            ext = os.path.splitext(path)[1] or ".png"
            local = self.storage.resolve(path, os.path.join(root, f"overlay_{i}{ext}"))
            overlays.append({**item, "path": local, "kind": classify_media(path) or item.get("kind") or "image"})
        audio = []
        for i, item in enumerate(timeline.get("audio") or []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "").strip()
            if not path:
                continue
            ext = os.path.splitext(path)[1] or ".mp3"
            local = self.storage.resolve(path, os.path.join(root, f"audio_{i}{ext}"))
            audio.append({**item, "path": local})
        return texts, overlays, audio

    def _build_settings(self, job, media_local, clip_durations, cover_local, voice_path, music_path, work_dir, trim_starts=None, clip_focus=None, clip_zoom=None, target_duration=0.0):
        s = job.get("settings") or {}
        timeline_texts, timeline_overlays, timeline_audio = self._resolve_timeline(job, work_dir)
        from ..services import transcribe as transcribe_service

        transcriber = transcribe_service.make_transcriber(
            job.get("userId") or "",
            job.get("workspaceId") or "",
            self.storage,
            self.settings,
        )
        # Output shape: an explicit aspect ratio wins, then explicit pixels,
        # then RenderSettings' own default. Previously nothing was passed and
        # every product rendered 1080x1920 regardless of what the user asked
        # for, so "aspect" and width/height are threaded through here.
        aspect = (s.get("aspect") or "auto").strip()
        shape = {}
        if aspect != "auto":
            if not aspect_lib.is_valid(aspect):
                raise PipelineError(f"Unknown aspect ratio: {aspect}")
            shape["aspect"] = aspect
        elif s.get("width") or s.get("height"):
            # Sanitized here too: this value comes straight from stored job
            # settings, and an int() on a corrupted document would fail the job
            # before a single frame is rendered.
            shape["width"] = bounded_int(s.get("width"), VIDEO_WIDTH, 128, 4096)
            shape["height"] = bounded_int(s.get("height"), VIDEO_HEIGHT, 128, 4096)
            shape["aspect"] = "auto"
        return RenderSettings(
            **shape,
            script=job.get("script", ""),
            media_files=media_local,
            clip_durations=clip_durations,
            trim_starts=list(trim_starts or []),
            clip_focus=list(clip_focus or []),
            clip_zoom=list(clip_zoom or []),
            target_duration=target_duration or 0.0,
            preview=bool(s.get("preview")),
            hook_cover=cover_local,
            voice_path=voice_path,
            speech_rate=bounded_float(s.get("speech_rate", 1.0), 1.0, 0.5, 2.0),
            pacing=s.get("pacing", "Normal Sports Story"),
            hook=bool(s.get("hook", True)),
            hook_style=s.get("hook_style", "Breaking News"),
            hook_text=s.get("hook_text", ""),
            hook_subtext=s.get("hook_subtext", ""),
            hook_duration=bounded_float(s.get("hook_duration", 1.5), 1.5, 0.0, 30.0),
            captions=bool(s.get("captions", True)),
            transcribe_fn=transcriber,
            whisper_model_name=self.settings.whisper_model,
            caption_style=s.get("caption_style", "Karaoke"),
            font_size=bounded_int(s.get("font_size", 58), 58, 8, 240),
            words_per_line=bounded_int(s.get("words_per_line", 5), 5, 1, 20),
            caption_position=s.get("caption_position", "Bottom"),
            music_path=music_path,
            music_volume=clamp_music_volume(s.get("music_volume")),
            transition=s.get("transition", "Auto"),
            transition_duration=bounded_float(s.get("transition_duration", 0.5), 0.5, 0.1, 2.0),
            reframe=s.get("reframe", "auto"),
            color_brightness=bounded_float(s.get("color_brightness", 0.0), 0.0, -1.0, 1.0),
            color_contrast=bounded_float(s.get("color_contrast", 1.0), 1.0, 0.5, 2.0),
            color_saturation=bounded_float(s.get("color_saturation", 1.0), 1.0, 0.0, 2.0),
            normalize_audio=bool(s.get("normalize_audio", False)),
            page_name=(s.get("page_name") or "").strip() or (job.get("niche") or "").strip(),
            show_page_name=bool(s.get("show_page_name", True)),
            timeline_texts=timeline_texts,
            timeline_overlays=timeline_overlays,
            timeline_audio=timeline_audio,
            temp_dir=os.path.join(work_dir, "tmp"),
            output_path=os.path.join(work_dir, "final.mp4"),
            cache_dir=os.path.join(
                self.settings.storage_root, "cache", str(job.get("userId") or "anonymous")
            ),
        )

    def _get_whisper(self):
        if self._whisper_model is not None:
            return self._whisper_model
        with self._whisper_lock:
            if self._whisper_model is not None:
                return self._whisper_model
            import whisper  # type: ignore

            self._whisper_model = whisper.load_model(self.settings.whisper_model)
            return self._whisper_model

    def _publish(self, job_id, job, output_path, settings):
        uid = job.get("userId", "unknown")
        dest = f"{self.settings.video_prefix}/{uid}/{job_id}/final.mp4"
        url = as_absolute(
            self.storage.publish(output_path, dest),
            self.settings.public_url,
        )

        video_out = {
            "storagePath": dest,
            "url": url,
            "duration": round(get_duration(output_path), 2),
            "fileSize": os.path.getsize(output_path),
        }

        # Poster thumbnail: grab the hook frame from the finished video.
        poster_local = os.path.join(self.settings.work_root, job_id, "poster.jpg")
        try:
            run_cmd(
                [
                    "ffmpeg", "-y",
                    "-ss", "0.15",
                    "-i", output_path,
                    "-frames:v", "1",
                    "-q:v", "3",
                    poster_local,
                ]
            )
            if os.path.isfile(poster_local):
                poster_dest = f"{self.settings.video_prefix}/{uid}/{job_id}/poster.jpg"
                video_out["posterStoragePath"] = poster_dest
                video_out["posterUrl"] = as_absolute(
                    self.storage.publish(poster_local, poster_dest),
                    self.settings.public_url,
                )
                video_out["posterFileSize"] = os.path.getsize(poster_local)
        except Exception:
            pass  # poster is cosmetic; never fail the publish

        jobs_service.update_job(
            job_id,
            status="completed",
            stage="completed",
            progress=100,
            message="Your video is ready",
            output=video_out,
        )

    def _fail(self, job_id, exc):
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        try:
            log_dir = os.path.join(self.settings.storage_root, "logs")
            os.makedirs(log_dir, exist_ok=True)
            with open(
                os.path.join(log_dir, "worker_errors.log"), "a", encoding="utf-8"
            ) as f:
                f.write(f"[{dt.datetime.utcnow().isoformat()}] job={job_id} type={type(exc).__name__} str={str(exc)!r}\n{tb}\n")
        except Exception:
            pass
        message = self._trim_error(str(exc))
        try:
            jobs_service.update_job(
                job_id,
                status="failed",
                stage="failed",
                message=message,
                error=message,
            )
        except Exception:
            pass  # job was deleted while rendering

    @staticmethod
    def _trim_error(text: str) -> str:
        skip = (
            "ffmpeg version",
            "built with",
            "configuration:",
            "libavutil",
            "libavcodec",
            "libavformat",
            "libavdevice",
            "libavfilter",
            "libswscale",
            "libswresample",
            "libpostproc",
        )
        lines = [
            ln
            for ln in (text or "").splitlines()
            if not ln.strip().startswith(skip) or "Error" in ln
        ]
        cleaned = "\n".join(lines).strip()
        if not cleaned:
            cleaned = (text or "").strip()
        return cleaned[:500]