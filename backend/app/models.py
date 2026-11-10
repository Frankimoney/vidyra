"""API request/response models."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from pipeline.config import MUSIC_VOLUME


class JobCreate(BaseModel):
    title: str = Field(default="Untitled video", max_length=120)
    # Required for single-video jobs; derived from scenes for storyboards.
    script: str = Field(default="")
    # Each entry is a storage path (string) or {"path": str, "duration"?: float}.
    media: List[Any] = Field(default_factory=list)
    settings: Dict[str, Any] = Field(default_factory=dict)
    # Brand workspace whose defaults (voice, aspect, captions, music…) fill any
    # setting the request leaves blank. Optional; the seeded workspace is used.
    workspace_id: str = Field(default="", max_length=64)
    # Optional cover image used as the hook background + video thumbnail.
    cover: Optional[str] = None
    # Multi-scene storyboard: [{"script", "voice"?, "transition"?, "media": [...]}]
    # When present, each scene is rendered and the scenes are concatenated.
    scenes: Optional[List[Dict[str, Any]]] = None
    # Optional timeline finishing tracks: {"texts": [...], "overlays": [...],
    # "audio": [...]}. Applied once on the final cut (titles, logo/PiP, SFX).
    timeline: Optional[Dict[str, Any]] = None
    # Multi-character dialogue: [{"name": str, "voice": str}]. Lines in a scene
    # written as "Name: text" are spoken with that character's voice.
    # Characters are matched against the shared library by name, so a voice or
    # face saved there is picked up automatically.
    characters: Optional[List[Dict[str, Any]]] = None


class JobOut(BaseModel):
    job_id: str
    status: str
    stage: str
    progress: int
    message: str
    title: str
    created_at: str
    updated_at: str
    error: Optional[str] = None


class HealthOut(BaseModel):
    status: str
    version: str
    firebase: bool
    storage: Optional[str] = None


class CatalogOut(BaseModel):
    voices: List[str]
    music: List[str]
    presets: Dict[str, Any]
    speech_rate: Dict[str, float]


class CreateOut(BaseModel):
    job_id: str
    status: str = "queued"


class ProfileIn(BaseModel):
    page_name: str = Field(default="", max_length=64)


class TitleIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class WorkspaceBrand(BaseModel):
    primary: str = Field(default="", max_length=16)
    secondary: str = Field(default="", max_length=16)
    logo_path: str = Field(default="", max_length=400)


class WorkspaceDefaults(BaseModel):
    voice: Optional[str] = Field(default=None, max_length=300)
    speech_rate: Optional[float] = None
    pacing: Optional[str] = Field(default=None, max_length=60)
    hook: Optional[bool] = None
    hook_style: Optional[str] = Field(default=None, max_length=60)
    hook_text: Optional[str] = Field(default=None, max_length=200)
    hook_subtext: Optional[str] = Field(default=None, max_length=200)
    hook_duration: Optional[float] = None
    captions: Optional[bool] = None
    caption_style: Optional[str] = Field(default=None, max_length=60)
    font_size: Optional[float] = None
    words_per_line: Optional[float] = None
    caption_position: Optional[str] = Field(default=None, max_length=40)
    music: Optional[str] = Field(default=None, max_length=300)
    music_volume: Optional[float] = None
    transition: Optional[str] = Field(default=None, max_length=60)
    transition_duration: Optional[float] = None
    reframe: Optional[str] = Field(default=None, max_length=40)
    color_brightness: Optional[float] = None
    color_contrast: Optional[float] = None
    color_saturation: Optional[float] = None
    normalize_audio: Optional[bool] = None
    show_page_name: Optional[bool] = None
    page_name: Optional[str] = Field(default=None, max_length=64)
    aspect: Optional[str] = Field(default=None, max_length=8)


class WorkspaceIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    niche: str = Field(default="", max_length=80)
    page_name: str = Field(default="", max_length=64)
    brand: WorkspaceBrand = Field(default_factory=WorkspaceBrand)
    defaults: WorkspaceDefaults = Field(default_factory=WorkspaceDefaults)
    daily_runs: int = Field(default=0, ge=0)


class WorkspacePatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=80)
    niche: Optional[str] = Field(default=None, max_length=80)
    page_name: Optional[str] = Field(default=None, max_length=64)
    brand: Optional[WorkspaceBrand] = None
    defaults: Optional[WorkspaceDefaults] = None
    daily_runs: Optional[int] = Field(default=None, ge=0)


class WorkspaceMemberIn(BaseModel):
    email: str = Field(default="", max_length=200)
    uid: str = Field(default="", max_length=128)
    role: str = Field(default="viewer", max_length=16)


class StudioRunCreate(BaseModel):
    tool: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    prompt: str = Field(..., min_length=1)
    params: Dict[str, Any] = Field(default_factory=dict)
    # Media refs: storage paths (strings) or {"path": str} — one per required ref.
    refs: List[Any] = Field(default_factory=list)
    title: str = Field(default="", max_length=120)



class FilmCharacter(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)
    description: str = Field(default="", max_length=500)
    # Piper voice name, or "clone:<storagePath>" for a cloned voice. Empty →
    # Vidyra assigns one automatically.
    voice: str = Field(default="", max_length=300)


class CharacterIn(BaseModel):
    """Create/update a shared character identity."""
    name: str = Field(..., min_length=1, max_length=40)
    description: str = Field(default="", max_length=500)
    # Piper voice name, or "clone:<storagePath>" for a cloned voice.
    voice: str = Field(default="", max_length=300)
    # Storage path of the locked master face, reused by every feature.
    ref_image: str = Field(default="", max_length=400)


class CharacterOut(BaseModel):
    character_id: str
    name: str
    description: str
    voice: str
    ref_image: Optional[str] = None
    created_at: str
    updated_at: str


class FilmCreate(BaseModel):
    # Either a one-line `idea` (the agent writes everything) OR a full
    # `outline` production package — plain text or JSON, auto-detected.
    idea: str = Field(default="", max_length=2000)
    # Timeline finishing tracks applied after the film is assembled.
    timeline: Optional[Dict[str, Any]] = None
    outline: str = Field(default="", max_length=40000)
    style: str = Field(default="cinematic", max_length=40)
    scenes_count: int = Field(default=8, ge=2, le=24)
    characters: List[FilmCharacter] = Field(default_factory=list, max_length=8)
    captions: bool = Field(default=True)
    # Narrator voice: Piper name or "clone:<storagePath>".
    voice: str = Field(default="", max_length=300)
    # Built-in music catalog name (e.g. "pulse.wav") or a user-uploaded path.
    music: str = Field(default="", max_length=200)
    # Background music level, 0.0-1.0. Clamped server-side.
    music_volume: float = Field(default=MUSIC_VOLUME, ge=0.0, le=1.0)
    # Ground film facts in live web search when the agent writes the script.
    research: bool = Field(default=True)
    # Film dialogue scenes "as in a movie": the character moves (image-to-video)
    # and talks (lip-sync the scene speech). Off saves one GPU run per scene.
    lip_sync: bool = Field(default=True)
    # Output shape, from pipeline.aspect.ASPECTS (e.g. "16:9"); "auto" keeps the
    # default 9:16 canvas. Drives the generated stills *and* the final render.
    aspect: str = Field(default="auto", max_length=8)
    # Planning project this film belongs to. When set, the film reuses that
    # project's locked character portraits instead of re-shooting them, and
    # writes any new ones back — so faces stay consistent across the project.
    project_id: str = Field(default="", max_length=64)
    # Brand workspace supplying look/music/voice defaults the request leaves blank.
    workspace_id: str = Field(default="", max_length=64)


class ValidatePackageRequest(BaseModel):
    """Zero-cost check of a production package before any GPU is spent."""
    text: str = Field(..., min_length=1, max_length=40000)


class ProjectCreate(BaseModel):
    """Open a planning session with the director agent."""
    message: str = Field(..., min_length=1, max_length=8000)
    aspect: str = Field(default="auto", max_length=8)


class ProjectMessageIn(BaseModel):
    """One more turn of the planning conversation."""
    message: str = Field(..., min_length=1, max_length=8000)


class ProjectPlanIn(BaseModel):
    """The creator's edited production plan, saved before rendering."""
    plan: Dict[str, Any]


class ProjectRenderIn(BaseModel):
    """Links a planning project to the job it was finally filmed as."""
    job_id: str = Field(..., min_length=1, max_length=64)
