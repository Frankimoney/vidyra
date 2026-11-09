import { getAuth } from "firebase/auth";
import type {
  AddFacePayload,
  FaceRecordingUpload,
  LibraryItem,
  MusicUpload,
  PublicShare,
  RecipeDraft,
  RecipeMeta,
  UsageSummary,
  Workspace,
  StoryboardCharacterInput,
  StoryboardSceneInput,
  StudioCatalog,
  StudioRun,
  StudioTool,
} from "../types";

const ENV_API_URL: string | undefined = import.meta.env.VITE_API_URL;
if (!ENV_API_URL && window.location.protocol === "https:") {
  console.error(
    "[Vidyra] VITE_API_URL is missing on an HTTPS page — API calls will fail (mixed content). Set VITE_API_URL to your tunnel/VPS https URL."
  );
}
const API_URL: string =
  ENV_API_URL || `http://${window.location.hostname}:8000`;

const JSON_TIMEOUT_MS = 30000;
// The server cuts an uploaded video into 4s segments (a full re-encode) before
// it responds, so this has to cover transcode time, not just transfer time.
const UPLOAD_TIMEOUT_MS = 900000;

function fetchWithTimeout(
  url: string,
  init: RequestInit = {},
  timeoutMs: number = JSON_TIMEOUT_MS
): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  // Allow caller-passed signal to still abort via linking is overkill for solo;
  // our own controller covers dead-network hangs.
  return fetch(url, { ...init, signal: ctrl.signal }).finally(() =>
    clearTimeout(timer)
  );
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function authHeaders(forceRefresh = false): Promise<Record<string, string>> {
  const user = getAuth().currentUser;
  if (!user) throw new ApiError(401, "Not signed in.");
  return { Authorization: `Bearer ${await user.getIdToken(forceRefresh)}` };
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  timeoutMs: number = JSON_TIMEOUT_MS
): Promise<T> {
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> | undefined),
  };

  try {
    Object.assign(headers, await authHeaders());
  } catch {
    window.location.href = "/login";
    throw new ApiError(401, "Not signed in.");
  }

  return fetchWith(path, options, headers, true, timeoutMs);
}

/** Unauthenticated request (for the public share/watch endpoints). */
async function publicRequest<T>(path: string): Promise<T> {
  return fetchWith(path, {}, {});
}

async function fetchWith<T>(
  path: string,
  options: RequestInit,
  headers: Record<string, string>,
  retry401 = true,
  timeoutMs: number = JSON_TIMEOUT_MS
): Promise<T> {
  if (!(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  let res: Response;
  try {
    res = await fetchWithTimeout(`${API_URL}/api${path}`, { ...options, headers }, timeoutMs);
  } catch (e) {
    if ((e as Error).name === "AbortError")
      throw new ApiError(408, "Request timed out. Check your connection / tunnel URL.");
    throw new ApiError(0, "Network error. Backend unreachable — is the tunnel running?");
  }

  // 401 → force-refresh Firebase token once, then retry (token expiry race).
  if (res.status === 401 && retry401) {
    try {
      const fresh = await authHeaders(true);
      const retryHeaders = { ...headers, ...fresh };
      if (!(options.body instanceof FormData)) {
        retryHeaders["Content-Type"] = "application/json";
      }
      try {
        res = await fetchWithTimeout(`${API_URL}/api${path}`, {
          ...options,
          headers: retryHeaders,
        }, timeoutMs);
      } catch (e) {
        if ((e as Error).name === "AbortError")
          throw new ApiError(408, "Request timed out on retry.");
        throw e;
      }
    } catch {
      // fall through to normal error handling below
    }
  }

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      const raw = body.detail ?? body;
      if (Array.isArray(raw)) detail = raw.join(" • ");
      else if (typeof raw === "object" && Array.isArray(raw.issues))
        detail = raw.issues.join(" • ");
      else detail = typeof raw === "string" ? raw : JSON.stringify(raw);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;

  return (await res.json()) as T;
}

export interface UploadResult {
  storagePath: string;
  name: string;
  size: number;
  type: "image" | "video";
  /** Real clip length in seconds; video only. */
  duration?: number;
}

/** One upload may return several items (a video auto-splits into ~4s clips). */
export interface UploadResponse {
  items: UploadResult[];
  split: boolean;
}

export interface PackagePreview {
  valid: boolean;
  issues: string[];
  outline?: {
    title: string;
    style: string;
    characters: { name: string; description: string }[];
    scenes: {
      narration: string;
      dialogue: Record<string, string>;
      setting: string;
      shot: string;
      transition?: string;
    }[];
  };
  title?: string;
  charactersCount?: number;
  scenesCount?: number;
  runsNeeded?: number;
}

export interface ProjectMessage {
  role: "user" | "assistant";
  content: string;
  at?: string;
}

export interface ProjectPlanScene {
  narration: string;
  dialogue: Record<string, string>;
  setting: string;
  shot: string;
  transition?: string;
}

export interface ProjectPlan {
  title: string;
  style: string;
  instructions?: string;
  characters: { name: string; description: string; voice?: string }[];
  scenes: ProjectPlanScene[];
}

/** A character identity in the shared library, used by every feature. */
export interface SavedCharacter {
  character_id: string;
  name: string;
  description: string;
  voice: string;
  /** Storage path of the locked master face; reused instead of redrawing. */
  ref_image: string | null;
  created_at: string;
  updated_at: string;
}

/** A character identity locked for the whole project ("ingredient"). */
export interface ProjectCastMember {
  name: string;
  description: string;
  voice?: string;
  /** Storage path of the locked master portrait; null until first rendered. */
  portrait: string | null;
}

/** A planning project: the conversation plus the production plan it produced. */
export interface PlanningProject {
  projectId: string;
  title: string;
  status: "planning" | "planned" | "filmed";
  aspect: string;
  /** Fields the agent has settled so far (logline, tone, characters, …). */
  brief: Record<string, unknown>;
  /** What the agent still wants to know, if anything. */
  questions: string[];
  messages: ProjectMessage[];
  plan: ProjectPlan | null;
  /** Persistent character identities, reused across every film in the project. */
  cast: ProjectCastMember[];
  jobId: string | null;
  scenesCount: number;
  charactersCount: number;
  /** GPU runs the plan would cost; 0 until a plan exists. Locked portraits are free. */
  runsNeeded: number;
  createdAt: string;
  updatedAt: string;
}

export interface StudioQuota {
  max: number;
  used: number;
  remaining: number;
}

export const api = {
  health: () => request<{ status: string; firebase: boolean; storage?: string }>("/health"),
  catalog: () =>
    request<{
      voices: string[];
      music: string[];
      pacing: string[];
      hooks: string[];
      caption_styles: string[];
      caption_positions: string[];
      transitions: string[];
      speech_rate: { min: number; max: number };
    }>("/catalog/presets").catch(() => null),
  catalogVoices: () => request<{ voices: string[] }>("/catalog/voices"),
  catalogMusic: () => request<{ music: string[] }>("/catalog/music"),
  uploadFile: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<UploadResponse>("/uploads", { method: "POST", body: form });
  },
  /** Upload with progress + cancel (XHR — fetch can't report progress). */
  uploadFileWithProgress: (
    file: File,
    onProgress?: (pct: number) => void,
    signal?: AbortSignal
  ): Promise<UploadResponse> => {
    return (async () => {
      const user = getAuth().currentUser;
      if (!user) throw new ApiError(401, "Not signed in.");
      const token = await user.getIdToken();
      const form = new FormData();
      form.append("file", file);
      return new Promise<UploadResponse>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("POST", `${API_URL}/api/uploads`);
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
        xhr.timeout = UPLOAD_TIMEOUT_MS;
        if (signal) {
          if (signal.aborted) {
            xhr.abort();
            reject(new ApiError(0, "Upload cancelled."));
            return;
          }
          signal.addEventListener("abort", () => xhr.abort(), { once: true });
        }
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable && onProgress) {
            onProgress(Math.round((e.loaded / e.total) * 100));
          }
        };
        xhr.onload = () => {
          try {
            const body = xhr.responseText ? JSON.parse(xhr.responseText) : {};
            if (xhr.status >= 200 && xhr.status < 300) resolve(body as UploadResponse);
            else {
              const raw = body.detail ?? body;
              const msg =
                typeof raw === "string" ? raw : JSON.stringify(raw || xhr.statusText);
              reject(new ApiError(xhr.status, msg));
            }
          } catch {
            reject(new ApiError(xhr.status || 0, "Upload failed (bad response)."));
          }
        };
        xhr.onerror = () => reject(new ApiError(0, "Network error during upload."));
        xhr.ontimeout = () =>
          reject(
            new ApiError(408, "Upload timed out. Try a shorter or smaller video.")
          );
        xhr.onabort = () => reject(new ApiError(0, "Upload cancelled."));
        xhr.send(form);
      });
    })();
  },
  uploadFaceRecording: (blob: Blob, filename = "face.webm") => {
    const form = new FormData();
    form.append("file", blob, filename);
    return request<FaceRecordingUpload>("/uploads/face", { method: "POST", body: form });
  },
  uploadMusic: (file: File, purpose: "music" | "voice" = "music") => {
    const form = new FormData();
    form.append("file", file);
    form.append("purpose", purpose);
    return request<MusicUpload>("/uploads/music", { method: "POST", body: form });
  },
  addFaceRecording: (jobId: string, payload: AddFacePayload) =>
    request<{ jobId: string }>(`/jobs/${encodeURIComponent(jobId)}/face`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  preview: (path: string) =>
    request<{ url: string }>(`/uploads/preview?path=${encodeURIComponent(path)}`),
  createJob: (payload: {
    title: string;
    script: string;
    media: (string | { path: string; duration?: number | null; trimStart?: number | null })[];
    settings: Record<string, unknown>;
    cover?: string | null;
    scenes?: StoryboardSceneInput[];
    characters?: StoryboardCharacterInput[];
    workspace_id?: string;
  }) =>
    request<{ job_id: string; status: string }>("/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  createFilm: (payload: {
    idea: string;
    outline?: string;
    style: string;
    scenes_count: number;
    characters: { name: string; description: string; voice?: string }[];
    captions: boolean;
    music: string;
    /** Background music level, 0-1. Defaults to 0.35 server-side. */
    music_volume?: number;
    research: boolean;
    /** Piper name or "clone:<path>" — the film's narrator. */
    voice?: string;
    /** Output shape, e.g. "16:9". Omit for the vertical default. */
    aspect?: string;
    /** Planning project to join — reuses its locked character portraits. */
    project_id?: string;
    /** Skip image-to-video + lip-sync, saving one GPU run per scene. */
    lip_sync?: boolean;
    /** Brand workspace supplying look/voice defaults. */
    workspace_id?: string;
  }) =>
    request<{ job_id: string; status: string }>("/films", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  validatePackage: (text: string) =>
    request<PackagePreview>("/films/validate-package", {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  // ---- Recipes, workspaces, usage ----

  listRecipes: () => request<{ items: RecipeMeta[] }>("/recipes"),

  /** Drafting is free but may fact-check with search: allow up to 3 minutes. */
  draftRecipe: (
    recipeId: string,
    payload: { workspace_id?: string; aspect?: string; payload: Record<string, unknown> }
  ) =>
    request<RecipeDraft>(
      `/recipes/${encodeURIComponent(recipeId)}/draft`,
      { method: "POST", body: JSON.stringify(payload) },
      180000
    ),

  listWorkspaces: () => request<{ items: Workspace[] }>("/workspaces"),
  createWorkspace: (payload: {
    name: string;
    niche?: string;
    page_name?: string;
    brand?: { primary?: string; secondary?: string; logoPath?: string };
    defaults?: Record<string, unknown>;
    daily_runs?: number;
  }) =>
    request<Workspace>("/workspaces", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateWorkspace: (
    workspaceId: string,
    patch: {
      name?: string;
      niche?: string;
      page_name?: string;
      brand?: { primary?: string; secondary?: string; logoPath?: string };
      defaults?: Record<string, unknown>;
      daily_runs?: number;
    }
  ) =>
    request<Workspace>(`/workspaces/${encodeURIComponent(workspaceId)}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  usageSummary: (workspaceId = "", days = 30) => {
    const q = new URLSearchParams({ days: String(days) });
    if (workspaceId) q.set("workspace_id", workspaceId);
    return request<UsageSummary>(`/usage/summary?${q.toString()}`);
  },

  // ---- Planning agent (projects) ----

  /** Open a planning session; the agent replies with its first questions. */
  createProject: (message: string, aspect = "auto") =>
    request<{ project: PlanningProject; reply: string }>("/films/projects", {
      method: "POST",
      body: JSON.stringify({ message, aspect }),
    }),
  listProjects: () => request<{ items: PlanningProject[] }>("/films/projects"),
  getProject: (projectId: string) =>
    request<{ project: PlanningProject }>(
      `/films/projects/${encodeURIComponent(projectId)}`
    ),
  /** Continue the interview, or ask for a change once a plan exists. */
  sendProjectMessage: (projectId: string, message: string) =>
    request<{ project: PlanningProject; reply: string }>(
      `/films/projects/${encodeURIComponent(projectId)}/messages`,
      { method: "POST", body: JSON.stringify({ message }) }
    ),
  /** Persist the creator's edited plan (re-validated server-side). */
  saveProjectPlan: (projectId: string, plan: ProjectPlan) =>
    request<{ project: PlanningProject }>(
      `/films/projects/${encodeURIComponent(projectId)}/plan`,
      { method: "PATCH", body: JSON.stringify({ plan }) }
    ),
  /** Record which job the project was filmed as. */
  markProjectFilmed: (projectId: string, jobId: string) =>
    request<{ project: PlanningProject }>(
      `/films/projects/${encodeURIComponent(projectId)}/render`,
      { method: "POST", body: JSON.stringify({ job_id: jobId }) }
    ),
  deleteProject: (projectId: string) =>
    request<void>(`/films/projects/${encodeURIComponent(projectId)}`, {
      method: "DELETE",
    }),

  // ---- Shared character library ----
  // One identity per character across Auto Film, Storyboard, Studio and the
  // Director. Saving is an upsert by name, and omitted fields (voice,
  // ref_image) are preserved, so recording a new description never discards a
  // locked face.
  listCharacters: () => request<SavedCharacter[]>("/characters"),
  saveCharacter: (character: {
    name: string;
    description?: string;
    voice?: string;
    ref_image?: string;
  }) =>
    request<SavedCharacter>("/characters", {
      method: "POST",
      body: JSON.stringify(character),
    }),
  deleteCharacter: (characterId: string) =>
    request<void>(`/characters/${encodeURIComponent(characterId)}`, {
      method: "DELETE",
    }),
  createPodcast: (payload: {
    topic: string;
    turns_count: number;
    hosts: { name: string; description: string; voice?: string }[];
    /** Fallback voice for hosts without one: Piper name or "clone:<path>". */
    voice?: string;
    captions: boolean;
    lip_sync: boolean;
    music: string;
    /** Background music level, 0-1. Defaults to 0.35 server-side. */
    music_volume?: number;
    research: boolean;
    /** Output shape, e.g. "1:1". Omit for the vertical default. */
    aspect?: string;
  }) =>
    request<{ job_id: string; status: string }>("/podcasts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listJobs: () => request<JobMeta[]>("/jobs"),
  mediaUrl: (jobId: string) =>
    request<{ url: string }>(`/jobs/${encodeURIComponent(jobId)}/media-url`),
  shareUrl: (jobId: string) =>
    request<{ url: string; token: string; pageUrl: string }>(`/jobs/${encodeURIComponent(jobId)}/share-url`),
  deleteJob: (jobId: string) =>
    request<void>(`/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" }),
  renameJob: (jobId: string, title: string) =>
    request<{ title: string }>(`/jobs/${encodeURIComponent(jobId)}/title`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  getProfile: () => request<{ page_name: string }>("/me/profile"),
  saveProfile: (pageName: string) =>
    request<{ page_name: string }>("/me/profile", {
      method: "PUT",
      body: JSON.stringify({ page_name: pageName }),
    }),
  studioCatalog: () => request<StudioCatalog>("/catalog/studio"),
  library: (kind?: "image" | "video" | "audio", purpose?: "music" | "voice") => {
    const q = new URLSearchParams();
    if (kind) q.set("kind", kind);
    if (purpose) q.set("purpose", purpose);
    const qs = q.toString();
    return request<{ items: LibraryItem[] }>(`/library${qs ? `?${qs}` : ""}`);
  },
  createStudioRun: (payload: {
    tool: StudioTool;
    model: string;
    prompt: string;
    params?: Record<string, unknown>;
    refs?: (string | { path: string; role?: string })[];
    title?: string;
  }) =>
    request<{ run_id: string; status: string; fake?: boolean }>("/studio/runs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listStudioRuns: () => request<StudioRun[]>("/studio/runs"),
  studioQuota: () => request<StudioQuota>("/studio/quota"),
  deleteLibraryItem: (path: string) =>
    request<void>(
      `/media/${path.split("/").map(encodeURIComponent).join("/")}`,
      { method: "DELETE" }
    ),
  getStudioRun: (runId: string) =>
    request<StudioRun>(`/studio/runs/${encodeURIComponent(runId)}`),
  deleteStudioRun: (runId: string) =>
    request<void>(`/studio/runs/${encodeURIComponent(runId)}`, { method: "DELETE" }),
};

export const publicApi = {
  watch: (token: string) =>
    publicRequest<PublicShare>(`/public/video/${encodeURIComponent(token)}`),
};

export interface JobMeta {
  job_id: string;
  status: string;
  stage: string;
  progress: number;
  message: string;
  title: string;
  created_at: string;
  updated_at: string;
}