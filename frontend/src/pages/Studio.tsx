import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../services/api";
import { watchStudioRuns } from "../services/studioJobs";
import { useAuth } from "../contexts/AuthContext";
import { useConfirm, useToast } from "../components/feedback";
import { Badge, Button, Card, EmptyState, Field, Segmented, Spinner } from "../components/ui";
import { AssetPicker } from "../components/AssetPicker";
import CharacterMentions from "../components/CharacterMentions";
import AspectSelect, { aspectsFromCatalog } from "../components/AspectSelect";
import type { SavedCharacter } from "../services/api";
import type {
  StudioCatalog,
  StudioRun,
  StudioTool,
  VideoQuality,
} from "../types";

/* ------------------------------------------------------------------ */
/* Small helpers                                                       */
/* ------------------------------------------------------------------ */

/**
 * What the prompt box means for each tool. Lives in the UI so every tool reads
 * differently even if the backend catalog predates its hints (the catalog
 * carries the same copy for API clients; the UI copy wins on mismatch).
 */
const PROMPT_COPY: Record<StudioTool, { label: string; placeholder: string }> = {
  txt2img: {
    label: "Prompt",
    placeholder:
      "Describe the image you want to create — subject, setting, lighting, style, e.g. “a striker scoring at golden hour, cinematic, 8k”…",
  },
  img2img: {
    label: "Restyle direction",
    placeholder:
      "How to change the reference image — e.g. “ink-and-grain anime”, “vintage film still”, “pop-art poster”…",
  },
  character: {
    label: "Scene prompt",
    placeholder:
      "Describe the scene the locked character appears in — setting, action, lighting…",
  },
  voiceclone: {
    label: "What to say",
    placeholder:
      "The line the cloned voice should read, e.g. “Welcome back to the show.” — the voice sample supplies the voice.",
  },
  img2video: {
    label: "Motion prompt",
    placeholder:
      "How the still should move — e.g. “slow push-in”, “hair in the wind”, “camera drifts left”…",
  },
  lipsync: {
    label: "Scene prompt",
    placeholder:
      "The audio drives the mouth — add a short description of the shot if you need it.",
  },
};

function timeAgo(iso: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

/** Last-resort camera list; the backend catalog normally supplies this. */
const CAMERA_POSES = [
  "Zoom In",
  "Zoom Out",
  "Pan Left",
  "Pan Right",
  "Pan Up",
  "Pan Down",
  "ClockWise (CW)",
  "Anti Clockwise (ACW)",
  "Static",
];

type ParamField =
  | { key: string; label: string; min: number; max: number; step?: number }
  | { key: string; label: string; options: "aspect" | "camera_pose"; hint?: string };

type ParamMeta = {
  label: string;
  min?: number;
  max?: number;
  step?: number;
  options?: "aspect" | "camera_pose";
  hint?: string;
};

/**
 * Presentation for every parameter a model can actually receive. The catalog's
 * per-model defaults decide *which* of these render; this map only supplies
 * labels and sensible slider ranges. That keeps the UI from showing a control
 * the selected model ignores.
 */
const PARAM_META: Record<string, ParamMeta> = {
  aspect: { label: "Aspect ratio", options: "aspect", hint: "Wan centre-crops the subject to fill the frame." },
  width: { label: "Width", min: 256, max: 1536, step: 64 },
  height: { label: "Height", min: 256, max: 1536, step: 64 },
  frames: { label: "Frames", min: 16, max: 144, step: 8 },
  fps: { label: "FPS", min: 8, max: 30, step: 1 },
  steps: { label: "Steps", min: 1, max: 60, step: 1 },
  cfg: { label: "CFG", min: 1, max: 20, step: 0.5 },
  denoise: { label: "Denoise", min: 0, max: 1, step: 0.05 },
  camera_pose: { label: "Camera move", options: "camera_pose", hint: "Stamped into the Wan 2.2 camera trajectory." },
  camera_speed: { label: "Camera speed", min: 0.1, max: 2, step: 0.1 },
  exaggeration: { label: "Emotion", min: 0, max: 1, step: 0.05 },
  cfg_weight: { label: "Voice match", min: 0, max: 1, step: 0.05 },
  seed: { label: "Seed (same seed + prompt = same result)", min: 0, max: 99999999, step: 1 },
};

const PARAM_ORDER = [
  "aspect", "width", "height", "frames", "fps", "steps", "cfg", "denoise",
  "camera_pose", "camera_speed", "exaggeration", "cfg_weight", "seed",
];

function fieldsFor(defaults: Record<string, unknown> | undefined): ParamField[] {
  if (!defaults) return [];
  return PARAM_ORDER.filter((key) => key in defaults && key in PARAM_META).map((key) => {
    const meta = PARAM_META[key];
    if (meta.options) {
      return { key, label: meta.label, options: meta.options, hint: meta.hint };
    }
    return {
      key,
      label: meta.label,
      min: meta.min ?? 0,
      max: meta.max ?? 1,
      step: meta.step,
    };
  });
}

function applyQuality(
  params: Record<string, number | string>,
  quality: VideoQuality
): Record<string, number | string> {
  const steps = Number(params.steps);
  if (!Number.isFinite(steps) || !("steps" in params)) return params;
  const next = { ...params };
  if (quality === "draft") next.steps = Math.max(4, Math.round(steps * 0.6));
  if (quality === "high") next.steps = Math.min(60, Math.round(steps * 1.5));
  return next;
}

function fieldDefault(
  field: ParamField,
  defaults: Record<string, unknown>,
  cameraPoses: string[]
): number | string {
  const value = defaults[field.key];
  if ("options" in field) {
    if (typeof value === "string") return value;
    return field.options === "aspect" ? "auto" : cameraPoses[0];
  }
  return typeof value === "number" ? value : (field.min + field.max) / 2;
}

/* ------------------------------------------------------------------ */
/* Ref slot (upload or pick from library)                              */
/* ------------------------------------------------------------------ */

function RefSlot({
  label,
  kind,
  value,
  onPick,
  onClear,
  busy,
}: {
  label: string;
  kind: "image" | "video" | "audio";
  value: string | null;
  onPick: (storagePath: string) => void;
  onClear: () => void;
  busy: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const toast = useToast();

  async function loadPreview(path: string) {
    try {
      const { url } = await api.preview(path);
      setPreviewUrl(url);
    } catch {
      setPreviewUrl(null);
    }
  }

  useEffect(() => {
    if (value) loadPreview(value);
    else setPreviewUrl(null);
  }, [value]);

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    try {
      if (kind === "audio") {
        const res = await api.uploadMusic(file, "voice");
        if (res.storagePath) onPick(res.storagePath);
        else toast("Upload failed.", "error");
        return;
      }
      const res = await api.uploadFile(file);
      if (res.items.length > 0) onPick(res.items[0].storagePath);
      else toast("Upload failed.", "error");
    } catch (err) {
      toast((err as Error).message || "Upload failed.", "error");
    }
  }

  const accept = kind === "video" ? "video/*" : kind === "audio" ? "audio/*" : "image/*";
  const isVideo = kind === "video";
  const isAudio = kind === "audio";
  const icon = isVideo ? "🎬" : isAudio ? "🎵" : "🖼";

  return (
    <div className="field">
      <label className="field-label">{label}</label>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        style={{ display: "none" }}
        onChange={handleFile}
      />
      {value ? (
        <div style={{ position: "relative", borderRadius: 12, overflow: "hidden", border: "1px solid var(--line, rgba(255,255,255,0.12))" }}>
          {previewUrl ? (
            isVideo ? (
              <video src={previewUrl} muted playsInline preload="metadata" style={{ width: "100%", maxHeight: 220, objectFit: "cover", display: "block" }} />
            ) : isAudio ? (
              <audio src={previewUrl} controls style={{ width: "100%", padding: "18px 8px" }} />
            ) : (
              <img src={previewUrl} alt="" style={{ width: "100%", maxHeight: 220, objectFit: "cover", display: "block" }} />
            )
          ) : (
            <div style={{ padding: 24, textAlign: "center", color: "var(--text-3)" }}>{busy ? <Spinner /> : "…"}</div>
          )}
          <button
            type="button"
            onClick={onClear}
            style={{
              position: "absolute",
              top: 8,
              right: 8,
              width: 30,
              height: 30,
              borderRadius: 8,
              border: "none",
              background: "rgba(0,0,0,0.65)",
              color: "#fff",
              cursor: "pointer",
            }}
            title="Remove"
          >
            ✕
          </button>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            style={{
              flex: 1,
              padding: "26px 14px",
              borderRadius: 12,
              border: "1.5px dashed rgba(255,255,255,0.2)",
              background: "rgba(255,255,255,0.03)",
              color: "var(--text-3)",
              cursor: "pointer",
              fontSize: "0.9rem",
            }}
          >
            {icon} Upload {label.toLowerCase()}
          </button>
          <button
            type="button"
            onClick={() => setPickerOpen(true)}
            style={{
              flex: 1,
              padding: "26px 14px",
              borderRadius: 12,
              border: "1.5px dashed rgba(255,255,255,0.2)",
              background: "rgba(255,255,255,0.03)",
              color: "var(--text-3)",
              cursor: "pointer",
              fontSize: "0.9rem",
            }}
          >
            🗂 From library
          </button>
        </div>
      )}
      <AssetPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onPick={(item) => onPick(item.path)}
        kind={kind === "audio" ? "audio" : kind === "video" ? "video" : "image"}
        title={`Choose ${label.toLowerCase()}`}
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Result viewer                                                       */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* Research note                                                       */
/* ------------------------------------------------------------------ */

/**
 * Renders the provenance of an auto-researched run.
 *
 * Two cases the user must be able to tell apart:
 *  - a real photo was found and restyled, so the Commons attribution is owed;
 *  - no photo existed, so the figure is fictional and must never read as a
 *    photograph of a real person.
 */
function ResearchNote({ run }: { run: StudioRun }) {
  const research = run.research;
  const credits = research?.credits || run.credits;
  if (!research?.subject && !credits) return null;

  const subject = research?.subject?.trim();

  return (
    <div
      style={{
        marginTop: 10,
        padding: "8px 10px",
        borderRadius: 10,
        background: "var(--surface-2, rgba(127,127,127,0.08))",
        fontSize: "0.78rem",
        lineHeight: 1.45,
      }}
    >
      {subject && (
        <div>
          <strong>Researched:</strong> {subject}
          {research?.role ? ` — ${research.role}` : ""}
          {research?.grounded === false && (
            <span className="faint"> (from model knowledge — live search unavailable)</span>
          )}
        </div>
      )}

      {credits ? (
        <div className="faint" style={{ marginTop: 2 }}>
          Reference photo:{" "}
          {credits.page_url ? (
            <a href={credits.page_url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)" }}>
              {credits.title || "Wikimedia Commons"}
            </a>
          ) : (
            credits.title || "Wikimedia Commons"
          )}
          {credits.author ? ` · ${credits.author}` : ""}
          {credits.license ? ` · ${credits.license}` : ""} — restyled, not the original photo.
        </div>
      ) : (
        <div className="faint" style={{ marginTop: 2 }}>
          No reference photo was available, so this figure is <strong>fictional</strong> — an
          original character drawn in {subject || "the subject"}&apos;s colours and style, not a
          picture of a real person.
        </div>
      )}
    </div>
  );
}

export function RunResult({ run }: { run: StudioRun }) {
  const primary = run.output?.primary;
  if (!primary) return null;

  if (primary.kind === "image") {
    return <img src={primary.url} alt={run.title} style={{ width: "100%", borderRadius: 12, display: "block" }} />;
  }
  return (
    <video
      src={primary.url}
      controls
      playsInline
      preload="metadata"
      style={{ width: "100%", borderRadius: 12, display: "block", background: "#000" }}
    />
  );
}

/**
 * Turn a finished Studio image into a cast identity.
 *
 * The cast is the shared bridge into project cast: DirectorRoom lists saved
 * characters and, when one is added to a plan, projects_service adopts its
 * refImage as the project portrait. Studio outputs live under `studio/<uid>/`,
 * which the character route already accepts as an owned image reference.
 */
function SaveCastButton({ run }: { run: StudioRun }) {
  const toast = useToast();
  const primary = run.output?.primary;
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);

  if (!primary || primary.kind !== "image") return null;

  function start() {
    setName((run.title || primary!.name.replace(/\.[^.]+$/, "")).slice(0, 40));
    setDescription((run.prompt || "").slice(0, 500));
    setOpen(true);
  }

  async function save() {
    const trimmed = name.trim();
    if (!trimmed) {
      toast("Give the character a name.", "error");
      return;
    }
    setBusy(true);
    try {
      await api.saveCharacter({
        name: trimmed,
        description: description.trim(),
        ref_image: primary!.storagePath,
      });
      toast(`${trimmed} is in your cast.`, "success");
      setOpen(false);
    } catch (e) {
      toast((e as Error).message || "Could not save this character.", "error");
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <Button onClick={start} disabled={busy}>
        🎭 Add to cast
      </Button>
    );
  }

  return (
    <div
      style={{
        width: "100%",
        display: "grid",
        gap: 8,
        padding: 10,
        borderRadius: 10,
        border: "1px solid var(--line, rgba(255,255,255,0.12))",
      }}
    >
      <Field label="Character name">
        <input
          className="input"
          value={name}
          maxLength={40}
          placeholder="e.g. Maya"
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <Field label="Description">
        <input
          className="input"
          value={description}
          maxLength={500}
          placeholder="How they look and act"
          onChange={(e) => setDescription(e.target.value)}
        />
      </Field>
      <div style={{ display: "flex", gap: 8 }}>
        <Button variant="primary" loading={busy} onClick={save}>
          Save to cast
        </Button>
        <Button variant="ghost" onClick={() => setOpen(false)} disabled={busy}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Main Studio page                                                    */
/* ------------------------------------------------------------------ */

export default function Studio({
  initialTool,
  embedded = false,
  quality: controlledQuality,
}: {
  initialTool?: StudioTool;
  embedded?: boolean;
  quality?: VideoQuality;
} = {}) {
  const { uid } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const navigate = useNavigate();
  const [cancellingRun, setCancellingRun] = useState<string | null>(null);

  async function cancelRun(runId: string) {
    const ok = await confirm({
      title: "Stop this AI run?",
      message: "The GPU job will be cancelled so it stops billing.",
      confirmLabel: "Stop run",
      danger: true,
    });
    if (!ok) return;
    setCancellingRun(runId);
    try {
      await api.cancelStudioRun(runId);
      toast("Run cancelled.", "success");
    } catch (e) {
      toast((e as Error).message || "Could not cancel run.", "error");
    } finally {
      setCancellingRun(null);
    }
  }

  const [catalog, setCatalog] = useState<StudioCatalog | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [characters, setCharacters] = useState<SavedCharacter[]>([]);
  const [runs, setRuns] = useState<StudioRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);

  const [tool, setTool] = useState<StudioTool>(initialTool ?? "txt2img");
  const [localQuality, setLocalQuality] = useState<VideoQuality>("standard");
  const quality = controlledQuality ?? localQuality;
  const [model, setModel] = useState<string>("");
  const [prompt, setPrompt] = useState("");
  const [negative, setNegative] = useState("");
  const [params, setParams] = useState<Record<string, number | string>>({});
  const [refs, setRefs] = useState<Record<number, string | null>>({});
  const [submitting, setSubmitting] = useState(false);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);

  // Internet-grounded image mode — no reference image needed from the user.


  useEffect(() => {
    let cancelled = false;
    api
      .studioCatalog()
      .then((cat) => {
        if (cancelled) return;
        setCatalog(cat);
        const preferred = initialTool && cat.tools.some((t) => t.tool === initialTool)
          ? initialTool
          : cat.tools[0]?.tool;
        if (preferred) setTool(preferred);
      })
      .catch((e) => toast(e.message || "Could not load studio catalog.", "error"))
      .finally(() => !cancelled && setCatalogLoading(false));
    return () => {
      cancelled = true;
    };
  }, [initialTool, toast]);

  useEffect(() => {
    let cancelled = false;
    api
      .listCharacters()
      .then((items) => {
        if (!cancelled) setCharacters(items || []);
      })
      .catch(() => {
        if (!cancelled) setCharacters([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Realtime runs subscription
  useEffect(() => {
    if (!uid) return;
    setRunsLoading(true);
    const unsub = watchStudioRuns(uid, (list) => {
      setRuns(list);
      setRunsLoading(false);
    });
    return unsub;
  }, [uid]);

  const toolSpec = useMemo(() => catalog?.tools.find((t) => t.tool === tool), [catalog, tool]);
  const refSpecs = useMemo(
    () => catalog?.modelRefs?.[tool]?.[model] ?? toolSpec?.refs ?? [],
    [catalog, tool, model, toolSpec]
  );
  const modelDefaults = useMemo(
    () => (catalog?.modelParams?.[tool]?.[model] || {}) as Record<string, unknown>,
    [catalog, tool, model]
  );
  const cameraPoses = useMemo(
    () => catalog?.paramOptions?.camera_pose?.length
      ? catalog.paramOptions.camera_pose
      : CAMERA_POSES,
    [catalog]
  );
  const fields = useMemo(() => fieldsFor(modelDefaults), [modelDefaults]);
  const supportsQuality = "steps" in modelDefaults;
  const promptSupported = "prompt" in modelDefaults;
  const negativeSupported = "negative" in modelDefaults;
  const toolLabels = useMemo(() => {
    const out: Partial<Record<StudioTool, string>> = {};
    for (const item of catalog?.tools || []) out[item.tool] = item.label;
    return out;
  }, [catalog]);

  // When tool/model changes, reset model + params to defaults
  useEffect(() => {
    if (!toolSpec) return;
    const firstModel = toolSpec.models[0];
    setModel(firstModel);
    const defaults = (catalog?.modelParams?.[tool]?.[firstModel] || {}) as Record<string, unknown>;
    const next: Record<string, number | string> = {};
    for (const f of fieldsFor(defaults)) {
      next[f.key] = fieldDefault(f, defaults, cameraPoses);
    }
    setParams(next);
    setRefs({});
    setNegative("negative" in defaults ? String(defaults.negative || "") : "");
  }, [tool, catalog, cameraPoses]);

  function handleModelChange(m: string) {
    setModel(m);
    setRefs({});
    const defaults = (catalog?.modelParams?.[tool]?.[m] || {}) as Record<string, unknown>;
    const next: Record<string, number | string> = {};
    for (const f of fieldsFor(defaults)) {
      next[f.key] = fieldDefault(f, defaults, cameraPoses);
    }
    setParams(next);
    setNegative("negative" in defaults ? String(defaults.negative || "") : "");
  }

  function handleMention(character: SavedCharacter) {
    if (!character.ref_image) {
      toast(`@${character.name} has no locked photo yet. Add one in Cast.`, "info");
      return;
    }
    const imageSlots = refSpecs
      .map((slot, index) => ({ slot, index }))
      .filter(({ slot }) => slot.kind === "image");
    if (imageSlots.length === 0) {
      toast("This tool has no image reference slot; the character name was added to the prompt.", "info");
      return;
    }
    const target = imageSlots.find(({ index }) => !refs[index]) ?? imageSlots[0];
    setRefs((prev) => ({ ...prev, [target.index]: character.ref_image }));
    toast(`Attached ${character.name}'s locked face.`, "success");
  }

  async function submit() {
    if (promptSupported && !prompt.trim()) {
      toast("Enter a prompt first.", "error");
      return;
    }
    const missing = refSpecs.filter((slot, i) => slot.required && !refs[i]);
    if (missing.length) {
      toast(`Please provide: ${missing.map((m) => m.label).join(", ")}`, "error");
      return;
    }
    const refList = refSpecs
      .map((slot, i) => (refs[i] ? { path: refs[i] as string, role: slot.role } : null))
      .filter((x): x is { path: string; role: string } => !!x);

    // A chosen ratio wins over the manual pixel sliders, so send only the
    // ratio and let the backend derive the canvas — sending both would be
    // ambiguous about which one the run should honour.
    const paramsToSend: Record<string, number | string> = applyQuality({ ...params }, quality);
    if (negativeSupported) paramsToSend.negative = negative;
    const ratioLocked = Boolean(params.aspect && params.aspect !== "auto");
    if (ratioLocked) {
      delete paramsToSend.width;
      delete paramsToSend.height;
    }

    setSubmitting(true);
    try {
      const cleanPrompt = promptSupported ? prompt.trim() : "";
      const res = await api.createStudioRun({
        tool,
        model,
        prompt: cleanPrompt,
        params: paramsToSend,
        refs: refList,
        title: cleanPrompt.slice(0, 60) || toolSpec?.label || tool,
      });
      setSelectedRun(res.run_id);
      toast(res.fake ? "Started (fake GPU — local placeholder output)." : "Queued on RunPod GPU.", "success");
      setPrompt("");
      setRefs({});
    } catch (e) {
      toast((e as Error).message || "Failed to start run.", "error");
    } finally {
      setSubmitting(false);
    }
  }


  async function removeRun(runId: string) {
    try {
      await api.deleteStudioRun(runId);
      if (selectedRun === runId) setSelectedRun(null);
      toast("Run deleted.", "success");
    } catch (e) {
      toast((e as Error).message || "Delete failed.", "error");
    }
  }


  // Tell the user what the chosen shape actually renders at, so a ratio is
  // never a guess. Aspect is resolved server-side; this mirrors that math.
  const shapeSummary = useMemo(() => {
    const modelDefaults = catalog?.modelParams?.[tool]?.[model] as
      | Record<string, unknown>
      | undefined;
    const w0 = Number(params.width ?? modelDefaults?.width ?? 0);
    const h0 = Number(params.height ?? modelDefaults?.height ?? 0);
    if (!w0 || !h0) return "";
    if (params.aspect && params.aspect !== "auto") {
      const ratio = String(params.aspect).split(":").map(Number);
      if (ratio.length === 2 && ratio[0] > 0 && ratio[1] > 0) {
        const long = Math.max(w0, h0);
        const snap = (n: number) => Math.max(16, Math.round(n / 16) * 16);
        const dims =
          ratio[0] >= ratio[1]
            ? { w: snap(long), h: snap((long * ratio[1]) / ratio[0]) }
            : { w: snap((long * ratio[0]) / ratio[1]), h: snap(long) };
        return `Output ${dims.w} x ${dims.h} px (${params.aspect})`;
      }
    }
    return `Output ${w0} x ${h0} px`;
  }, [params.width, params.height, params.aspect, catalog, tool, model]);

  if (catalogLoading) {
    return (
      <div style={{ display: "grid", placeItems: "center", minHeight: "40dvh" }}>
        <Spinner />
      </div>
    );
  }

  const disabled = !catalog?.enabled;

  return (
    <div className="fade-up">
      {!embedded && (
        <header style={{ marginBottom: 18 }}>
          <h2 style={{ marginTop: 0 }}>AI Studio</h2>
          <p className="faint" style={{ fontSize: "0.88rem", marginTop: -4 }}>
            {disabled
              ? "GPU not configured — set RUNPOD_API_KEY + RUNPOD_ENDPOINT_ID on the backend to enable."
              : catalog?.fake
              ? "Running in fake-GPU mode (local placeholder outputs, no RunPod charges)."
              : "Powered by RunPod Serverless GPU. Each run bills GPU seconds."}
          </p>
        </header>
      )}

      {disabled ? (
        <EmptyState icon="🔌" title="AI Studio is offline" hint="Configure the backend RunPod credentials to unlock the six AI tools." />
      ) : (
        <div style={{ display: "grid", gap: 18, gridTemplateColumns: "minmax(0, 1fr)" }}>
          <Card>
            {!embedded && (
              <Segmented
                options={(catalog?.tools || []).map((t) => t.tool as StudioTool)}
                labels={toolLabels}
                value={tool}
                onChange={(t) => setTool(t)}
              />
            )}

            <div style={{ marginTop: embedded ? 0 : 14 }}>
              <Field label="Model">
                <select
                  className="input"
                  value={model}
                  onChange={(e) => handleModelChange(e.target.value)}
                >
                  {(toolSpec?.models || []).map((m) => (
                    <option key={m} value={m}>
                      {catalog?.modelLabels?.[m] || m}
                    </option>
                  ))}
                </select>
              </Field>
            </div>

            {supportsQuality && !embedded && (
              <div style={{ marginTop: 12 }}>
                <div className="faint" style={{ fontSize: "0.8rem", marginBottom: 4 }}>
                  Video quality
                </div>
                <div className="segmented">
                  {(["draft", "standard", "high"] as VideoQuality[]).map((option) => (
                    <button
                      key={option}
                      type="button"
                      className={`seg-btn ${quality === option ? "active" : ""}`}
                      onClick={() => setLocalQuality(option)}
                    >
                      {option === "draft" ? "Draft" : option === "standard" ? "Standard" : "High"}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {promptSupported && (
              <div style={{ marginTop: 6 }}>
                <Field label={PROMPT_COPY[tool]?.label || toolSpec?.promptLabel || "Prompt"}>
                  <CharacterMentions
                    value={prompt}
                    onChange={setPrompt}
                    characters={characters}
                    onMention={handleMention}
                    rows={3}
                    placeholder={
                      PROMPT_COPY[tool]?.placeholder ||
                      toolSpec?.promptHint ||
                      "Describe the video you want (style, motion, mood)…"
                    }
                  />
                  {characters.length > 0 && (
                    <p className="faint" style={{ fontSize: "0.76rem", margin: "4px 0 0" }}>
                      Type @ to use a saved character.
                    </p>
                  )}
                </Field>
              </div>
            )}

            {negativeSupported && (
              <div style={{ marginTop: 6 }}>
                <Field label="Negative prompt (optional)">
                  <input
                    className="input"
                    placeholder="blurry, low quality, watermark…"
                    value={negative}
                    onChange={(e) => setNegative(e.target.value)}
                  />
                </Field>
              </div>
            )}
            {(tool === "txt2img" || tool === "img2img") && (
              <p className="faint" style={{ fontSize: "0.78rem", margin: "2px 0 0" }}>
                Name a real person, team or place — or paste an article — and Vidyra
                researches it automatically: if a freely licensed photo exists it is
                restyled into your graphic, otherwise an original character in that
                subject&apos;s own colours and symbols is drawn instead.
              </p>
            )}

            {/* Media refs */}
            {refSpecs.length > 0 && (
              <div style={{ marginTop: 6 }}>
                {refSpecs.map((slot, i) => (
                  <RefSlot
                    key={`${tool}-${slot.role}`}
                    label={slot.required ? slot.label : `${slot.label} (optional)`}
                    kind={slot.kind}
                    value={refs[i] || null}
                    onPick={(path) => setRefs((prev) => ({ ...prev, [i]: path }))}
                    onClear={() => setRefs((prev) => ({ ...prev, [i]: null }))}
                    busy={submitting}
                  />
                ))}
              </div>
            )}

            {/* Numeric params */}
            {fields.length > 0 && (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12, marginTop: 8 }}>
                {fields.map((f) => {
                  // Raw pixel controls are redundant once a ratio is chosen —
                  // the backend derives width/height from it — so they hide.
                  if (
                    (f.key === "width" || f.key === "height") &&
                    params.aspect &&
                    params.aspect !== "auto"
                  ) {
                    return null;
                  }
                  if ("options" in f) {
                    if (f.options === "aspect") {
                      const opts = aspectsFromCatalog(catalog?.paramOptions);
                      return (
                        <AspectSelect
                          key={f.key}
                          id={`studio-${f.key}`}
                          label={f.label}
                          hint={f.hint}
                          choices={opts}
                          value={String(params[f.key] ?? "auto")}
                          onChange={(v) =>
                            setParams((prev) => ({ ...prev, [f.key]: v }))
                          }
                        />
                      );
                    }
                    return (
                      <Field key={f.key} label={f.label}>
                        <select
                          className="input"
                          value={String(params[f.key] ?? cameraPoses[0])}
                          onChange={(e) =>
                            setParams((prev) => ({ ...prev, [f.key]: e.target.value }))
                          }
                        >
                          {cameraPoses.map((pose) => (
                            <option key={pose} value={pose}>{pose}</option>
                          ))}
                        </select>
                        {f.hint && (
                          <p className="faint" style={{ fontSize: "0.78rem", margin: "2px 0 0" }}>
                            {f.hint}
                          </p>
                        )}
                      </Field>
                    );
                  }
                  return (
                    <div key={f.key}>
                      <div className="spread" style={{ fontSize: "0.8rem", marginBottom: 4 }}>
                        <span className="faint">{f.label}</span>
                        <span style={{ fontWeight: 700 }}>{params[f.key] ?? "—"}</span>
                      </div>
                      <input
                        type="range"
                        className="slider"
                        min={f.min}
                        max={f.max}
                        step={f.step || 1}
                        value={params[f.key] ?? f.min}
                        onChange={(e) =>
                          setParams((prev) => ({ ...prev, [f.key]: Number(e.target.value) }))
                        }
                        style={{ width: "100%" }}
                      />
                    </div>
                  );
                })}
                {shapeSummary && (
                  <p
                    className="faint"
                    style={{ fontSize: "0.78rem", margin: "2px 0 0", fontWeight: 600 }}
                  >
                    {shapeSummary}
                  </p>
                )}
              </div>
            )}

            <div style={{ marginTop: 16 }}>
              <Button variant="primary" size="lg" loading={submitting} onClick={submit}>
                {catalog?.fake ? "Run (fake GPU)" : "Run on GPU"}
              </Button>
              {!catalog?.fake && (
                <p className="faint" style={{ fontSize: "0.78rem", margin: "6px 0 0" }}>
                  Estimated cost: ~$
                  {(catalog?.prices?.[model] ?? catalog?.defaultPrice ?? 0.05).toFixed(3)}
                  {" "}per run. Final billing comes from your GPU provider.
                </p>
              )}
            </div>
          </Card>

          {/* Runs live in Library when Studio is embedded in Create. */}
          {!embedded && (
          <div>
            <h3 className="section-title" style={{ marginBottom: 10 }}>
              Recent runs
            </h3>
            {runsLoading ? (
              <EmptyState icon="⏳" title="Loading runs…" />
            ) : runs.length === 0 ? (
              <EmptyState icon="🎬" title="No studio runs yet" hint="Your generated images and videos will appear here." />
            ) : (
              <div className="stack-v">
                {runs.map((run) => (
                  <Card key={run.run_id}>
                    <div className="spread">
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div
                          style={{
                            fontWeight: 700,
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                          }}
                        >
                          {run.title || run.prompt || "Untitled"}
                        </div>
                        <div className="faint" style={{ fontSize: "0.8rem", marginTop: 2 }}>
                          {run.tool} · {run.model} · {timeAgo(run.created_at)}
                        </div>
                        {run.credits && (
                          <div
                            className="faint"
                            style={{ fontSize: "0.75rem", marginTop: 2, wordBreak: "break-word" }}
                          >
                            Ref: {run.credits.title} · {run.credits.license}
                          </div>
                        )}
                      </div>
                      <Badge status={run.status} />
                    </div>

                    {(run.status === "processing" || run.status === "queued") && (
                      <div style={{ marginTop: 10 }}>
                        <div className="progress-track">
                          <div className="progress-fill" style={{ width: `${run.progress}%` }} />
                        </div>
                        <div style={{ marginTop: 8, textAlign: "right" }}>
                          <Button
                            variant="ghost"
                            onClick={() => cancelRun(run.run_id)}
                            disabled={cancellingRun === run.run_id}
                          >
                            {cancellingRun === run.run_id ? "Stopping…" : "Stop"}
                          </Button>
                        </div>
                      </div>
                    )}

                    {(run.status === "failed" || run.status === "cancelled") && (
                      <div style={{ marginTop: 10 }}>
                        {run.error && (
                          <div
                            style={{
                              fontSize: "0.84rem",
                              color:
                                run.status === "cancelled"
                                  ? "var(--text-3)"
                                  : "var(--danger, #f87171)",
                              whiteSpace: "pre-wrap",
                            }}
                          >
                            {run.error}
                          </div>
                        )}
                        <div style={{ marginTop: 8, textAlign: "right" }}>
                          <button
                            type="button"
                            onClick={() => removeRun(run.run_id)}
                            style={{
                              background: "none",
                              border: "none",
                              color: "var(--text-3)",
                              cursor: "pointer",
                              fontSize: "0.84rem",
                            }}
                          >
                            Delete
                          </button>
                        </div>
                      </div>
                    )}

                    {run.status === "completed" && (
                      <div style={{ marginTop: 12 }}>
                        <RunResult run={run} />
                        <ResearchNote run={run} />
                        <div className="spread" style={{ marginTop: 8 }}>
                          <a
                            href={run.output?.primary?.url || "#"}
                            target="_blank"
                            rel="noreferrer"
                            style={{ color: "var(--accent)", fontSize: "0.85rem" }}
                          >
                            Open full size ↗
                          </a>
                          <button
                            type="button"
                            onClick={() => removeRun(run.run_id)}
                            style={{
                              background: "none",
                              border: "none",
                              color: "var(--text-3)",
                              cursor: "pointer",
                              fontSize: "0.84rem",
                            }}
                          >
                            Delete
                          </button>
                        </div>
                        {run.output?.primary && (
                          <div
                            style={{
                              marginTop: 10,
                              display: "flex",
                              gap: 8,
                              flexWrap: "wrap",
                              alignItems: "flex-start",
                            }}
                          >
                            <Button
                              variant="primary"
                              onClick={() => {
                                const p = run.output!.primary!;
                                navigate("/studio/scenes/editor", {
                                  state: {
                                    preload: {
                                      path: p.storagePath,
                                      name: p.name,
                                      kind: p.kind,
                                      size: p.size,
                                      url: p.url,
                                      duration: p.duration,
                                    },
                                  },
                                });
                              }}
                            >
                              ➕ Use in video
                            </Button>
                            <SaveCastButton run={run} />
                          </div>
                        )}
                      </div>
                    )}
                  </Card>
                ))}
              </div>
            )}
          </div>
          )}
        </div>
      )}
    </div>
  );
}