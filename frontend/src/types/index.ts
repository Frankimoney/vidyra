/* ---------- Shared domain types ---------- */

export type JobStatus = "queued" | "processing" | "completed" | "failed" | "cancelled";

/** Shared render effort preset: fewer steps for drafts, more for finals. */
export type VideoQuality = "draft" | "standard" | "high";

export interface MediaClipInput {
  path: string;
  duration?: number | null;
  /** Video-only: start offset in seconds into the source file (trim in-point). */
  trimStart?: number | null;
  /** Crop focal point, 0-1 across the source (0.5 = center). */
  focusX?: number | null;
  focusY?: number | null;
  /** Crop zoom multiplier, 1-3 (1 = fill, >1 = punch in). */
  zoom?: number | null;
  /** Video-only playback speed, 0.25-4. */
  speed?: number | null;
  reverse?: boolean | null;
  freeze?: boolean | null;
  /** Image motion preset: auto | none | zoomIn | zoomOut | panLeft | panRight. */
  motion?: string | null;
  /** Animated crop/zoom keyframes (t 0-1, zoom, focusX, focusY). */
  keyframes?: { t: number; zoom: number; focusX: number; focusY: number }[] | null;
}

/* ---------- Storyboard (multi-scene + multi-character) ---------- */

export interface StoryboardSceneInput {
  script: string;
  voice?: string;
  transition?: string;
  /** Explicit scene length in seconds. Omit for narration-driven timing. */
  duration?: number | null;
  media: MediaClipInput[];
}

export interface StoryboardCharacterInput {
  name: string;
  voice: string;
}

export interface JobItem {
  jobId: string;
  title: string;
  script: string;
  scriptWordCount?: number;
  media: (string | MediaClipInput)[];
  scenes?: StoryboardSceneInput[];
  characters?: StoryboardCharacterInput[];
  cover?: string | null;
  settings?: Record<string, unknown>;
  niche?: string;
  type?: "video" | "face";
  parentJobId?: string;
  filmPlot?: any;
  film?: {
    scenes_count?: number;
    outline?: { scenes?: unknown[] };
  };
  /** Which storyboard scene the worker is filming right now (1-based). */
  sceneIndex?: number;
  /** Total scenes in this film, reported by the worker. */
  sceneCount?: number;
  timeline?: TimelineTracks;
  workspaceId?: string;
  status: JobStatus;
  stage: string;
  progress: number;
  message: string;
  createdAt: string;
  updatedAt: string;
  output?: {
    storagePath: string;
    url: string;
    duration: number;
    fileSize: number;
    posterStoragePath?: string;
    posterUrl?: string;
    posterFileSize?: number;
  } | null;
  error?: string | null;
}

export interface UploadedMedia {
  id: string;
  name: string;
  type: "image" | "video";
  url: string;
  size: number;
  storagePath: string;
  duration?: number;
  objectUrl?: string;
}

export interface LibraryItem {
  path: string;
  name: string;
  kind: "image" | "video" | "audio";
  size: number;
  created: number;
  source: "media" | "studio" | "videos";
  /** Audio only: whether the file is a music track or a voice sample. */
  purpose?: "music" | "voice";
  url: string;
}

/** An asset handed to the Create page via navigation state (e.g. "Use in video"). */
export interface PreloadMedia {
  path: string;
  name: string;
  kind: "image" | "video";
  size?: number;
  url?: string;
  duration?: number;
}

export interface EditableClip {
  path: string;
  name: string;
  type: "image" | "video";
  url: string;
  duration?: number;
  /** Video-only trim in-point (seconds into source). Images ignore it. */
  trimStart?: number;
  /** Crop focal point, 0-1 across the source (0.5 = center). */
  focusX?: number;
  focusY?: number;
  /** Crop zoom multiplier, 1-3 (1 = fill, >1 = punch in). */
  zoom?: number;
  speed?: number;
  reverse?: boolean;
  freeze?: boolean;
  motion?: string;
  keyframes?: { t: number; zoom: number; focusX: number; focusY: number }[];
}

export interface Catalog {
  voices: string[];
  music: string[];
  pacing: string[];
  hooks: string[];
  captionStyles: string[];
  captionPositions: string[];
  transitions: string[];
  speechRate: { min: number; max: number };
}

/* ---------- Workspaces, recipes, usage ---------- */

export interface WorkspaceBrand {
  primary: string;
  secondary: string;
  logoPath: string;
}

export interface WorkspaceMember {
  uid: string;
  email: string;
  role: "editor" | "viewer";
}

export interface Workspace {
  workspace_id: string;
  name: string;
  niche: string;
  page_name: string;
  brand: WorkspaceBrand;
  defaults: Record<string, unknown>;
  budget: { dailyRuns: number };
  members?: WorkspaceMember[];
  created_at?: string;
  updated_at?: string;
}

export interface RecipeField {
  name: string;
  label: string;
  type: "text" | "textarea" | "number" | "url";
  placeholder?: string;
  required?: boolean;
  default?: string | number;
  min?: number;
  max?: number;
}

export interface RecipeMeta {
  id: string;
  name: string;
  description: string;
  fields: RecipeField[];
}

export interface RecipeSource {
  title?: string;
  url: string;
}

export interface RecipeScene {
  narration: string;
  dialogue?: Record<string, string>;
  setting: string;
  shot?: string;
  transition?: string;
}

export interface RecipeOutline {
  title: string;
  style: string;
  instructions?: string;
  characters: { name: string; description: string; voice?: string }[];
  scenes: RecipeScene[];
  sources?: RecipeSource[];
}

export interface RecipeDraft {
  recipe_id: string;
  outline: RecipeOutline;
  issues: string[];
  sources: RecipeSource[];
  fact_check: {
    checked: boolean;
    sources: RecipeSource[];
    corrected: number;
    notes: string[];
    error: string | null;
  };
  provider: string;
  estimate: { scenes: number; gpu_runs: number };
  article?: {
    title: string;
    url: string;
    site: string;
    word_count: number;
    truncated: boolean;
  };
  workspace?: {
    workspace_id: string;
    name: string;
    page_name: string;
    brand: WorkspaceBrand;
    budget: { dailyRuns: number };
  };
  defaults: Record<string, unknown>;
}

export interface TimelineText {
  id: string;
  text: string;
  start: number;
  end: number;
  x: number;
  y: number;
  size: number;
  color: string;
  background: boolean;
  animation?: "none" | "fade";
}

export interface TimelineOverlay {
  id: string;
  path: string;
  name: string;
  kind: "image" | "video";
  url: string;
  start: number;
  end: number;
  x: number;
  y: number;
  width: number;
  opacity: number;
}

export interface TimelineAudio {
  id: string;
  path: string;
  name: string;
  url: string;
  start: number;
  end: number;
  volume: number;
  fadeIn: number;
  fadeOut: number;
}

export interface TimelineTracks {
  texts: TimelineText[];
  overlays: TimelineOverlay[];
  audio: TimelineAudio[];
}

export interface Series {
  series_id: string;
  name: string;
  workspace_id: string;
  interval_hours: number;
  enabled: boolean;
  next_run_at: string;
  created_at: string;
  updated_at: string;
}

export interface RecipeHandoff {
  recipe_id: string;
  outline: RecipeOutline;
  defaults: Record<string, unknown>;
  workspace?: RecipeDraft["workspace"];
  estimate: { scenes: number; gpu_runs: number };
}

export interface UsageEntry {
  usageId: string;
  workspaceId: string;
  kind: string;
  tool: string;
  model: string;
  units: number;
  jobId: string;
  reference: string;
  estimatedCost: number;
  createdAt: string;
}

export interface UsageSummary {
  days: number;
  workspace_id: string;
  today_runs: number;
  runs: number;
  estimated_cost: number;
  by_model: { model: string; runs: number; cost: number }[];
  recent: UsageEntry[];
}

export interface VideoSettings {
  voice: string;
  speech_rate: number;
  pacing: string;
  hook: boolean;
  hook_style: string;
  hook_text: string;
  hook_subtext: string;
  hook_duration: number;
  captions: boolean;
  caption_style: string;
  font_size: number;
  words_per_line: number;
  caption_position: string;
  music: string | null;
  /** Background music level, 0-1. Defaults to 0.35 server-side. */
  music_volume?: number;
  transition: string;
  transition_duration?: number;
  reframe?: string;
  color_brightness?: number;
  color_contrast?: number;
  color_saturation?: number;
  normalize_audio?: boolean;
  /** Output shape for generated visuals and the final cut, e.g. "16:9". */
  aspect?: string;
  show_page_name?: boolean;
  page_name?: string;
}

export interface PublicShare {
  jobId: string;
  title: string;
  url: string;
  poster: string | null;
  pageName: string;
  duration: number;
  createdAt: string;
}

export type FaceShape = "circle" | "rounded" | "rect";

export interface FaceOverlay {
  shape: FaceShape;
  scale: number;
  x: number;
  y: number;
  recordingWidth: number;
  recordingHeight: number;
}

export interface FaceRecordingUpload {
  storagePath: string;
  duration: number;
  size: number;
  type: string;
}

export interface MusicUpload {
  storagePath: string;
  name: string;
  duration: number;
  size: number;
  type: string;
  purpose?: "music" | "voice";
}

export interface AddFacePayload {
  recordingPath: string;
  duration: number;
  overlay: FaceOverlay;
  includeAudio: boolean;
}

/* ---------- AI Studio ---------- */

export type StudioTool =
  | "txt2img"
  | "img2img"
  | "character"
  | "img2video"
  | "lipsync"
  | "voiceclone";

export interface StudioRefSpec {
  role: string;
  label: string;
  required: boolean;
  kind: "image" | "video" | "audio";
}

export interface StudioToolSpec {
  tool: StudioTool;
  label: string;
  kind: "image" | "video";
  models: string[];
  refs: StudioRefSpec[];
  /** Field label for the main prompt (each tool means something different by it). */
  promptLabel?: string;
  /** Per-tool placeholder so every prompt box stops reading the same. */
  promptHint?: string;
}

export interface StudioCatalog {
  enabled: boolean;
  fake: boolean;
  tools: StudioToolSpec[];
  modelParams: Record<string, Record<string, Record<string, unknown>>>;
  /** Per-model ref slots: modelRefs[tool][model] = StudioRefSpec[]. */
  modelRefs?: Record<string, Record<string, StudioRefSpec[]>>;
  /** Human-readable model names for the picker. */
  modelLabels?: Record<string, string>;
  /** Conservative per-run USD estimates for the cost hint. */
  prices?: Record<string, number>;
  defaultPrice?: number;
  /** Choice lists for enum params (aspect ratios) + their labels. */
  paramOptions?: {
    aspect?: string[];
    aspectLabels?: Record<string, string>;
    camera_pose?: string[];
  };
}

export interface StudioArtifact {
  name: string;
  kind: "image" | "video";
  storagePath: string;
  url: string;
  size: number;
  duration?: number;
}

export interface StudioRunOutput {
  artifacts?: StudioArtifact[];
  primary?: StudioArtifact;
  simulated?: boolean;
}

export interface StudioRun {
  run_id: string;
  title: string;
  tool: StudioTool;
  model: string;
  prompt: string;
  status: JobStatus;
  stage: string;
  progress: number;
  message: string;
  created_at: string;
  updated_at: string;
  output?: StudioRunOutput | null;
  error?: string | null;
  research?: {
    subject?: string;
    role?: string;
    fictional?: boolean;
    grounded?: boolean;
    source?: string;
    credits?: StudioCredits;
  } | null;
  credits?: StudioCredits | null;
}

export interface StudioCredits {
  title?: string;
  author?: string;
  license?: string;
  source_url?: string;
  page_url?: string;
}