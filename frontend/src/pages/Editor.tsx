import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../services/api";
import { useJob } from "../hooks/useJobs";
import { useAuth } from "../contexts/AuthContext";
import { useCloneVoices } from "../hooks/useCloneVoices";
import { useToast } from "../components/feedback";
import { Button, Card, EmptyState, Field, Spinner } from "../components/ui";
import VoiceSelect from "../components/VoiceSelect";
import { AssetPicker } from "../components/AssetPicker";
import { DEFAULT_SETTINGS, settingsFrom } from "../utils/settings";
import StepSettings from "../components/create/StepSettings";
import StepVisuals from "../components/create/StepVisuals";
import type {
  EditableClip,
  LibraryItem,
  StoryboardSceneInput,
  VideoSettings,
} from "../types";

/* ------------------------------------------------------------------ */
/* Constants + helpers                                                 */
/* ------------------------------------------------------------------ */

const DEFAULT_CLIP_SECONDS = 3;
const MAX_CLIP_SECONDS = 60;

interface SceneDraft {
  id: string;
  script: string;
  voice: string;
  transition: string;
  media: EditableClip[];
}

interface CharacterDraft {
  id: string;
  name: string;
  voice: string;
}

let _seq = 0;
const nextId = () => `e${Date.now().toString(36)}${(_seq++).toString(36)}`;

function typeOf(name: string): "image" | "video" {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  return ["mp4", "mov", "mkv", "webm"].includes(ext) ? "video" : "image";
}

function nameFromPath(path: string): string {
  return decodeURIComponent(path.split("/").pop() || "clip") || "clip";
}

function clipSeconds(clip: EditableClip): number {
  return Math.max(0.5, Number(clip.duration) || DEFAULT_CLIP_SECONDS);
}

function sceneSeconds(scene: SceneDraft): number {
  return scene.media.reduce((n, c) => n + clipSeconds(c), 0);
}

function mmss(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function libraryToClip(item: LibraryItem): EditableClip {
  return {
    path: item.path,
    name: item.name,
    type: item.kind === "video" ? "video" : "image",
    url: item.url,
    duration: DEFAULT_CLIP_SECONDS,
  };
}

/* ------------------------------------------------------------------ */
/* Focal crop modal                                                    */
/* ------------------------------------------------------------------ */

function FocalCropModal({
  clip,
  onClose,
}: {
  clip: EditableClip;
  onClose: (updated?: EditableClip) => void;
}) {
  const [fx, setFx] = useState(clip.focusX ?? 0.5);
  const [fy, setFy] = useState(clip.focusY ?? 0.5);
  const [zoom, setZoom] = useState(clip.zoom ?? 1);

  const style: React.CSSProperties = {
    width: "100%",
    height: "100%",
    objectFit: "cover",
    objectPosition: `${fx * 100}% ${fy * 100}%`,
    transform: `scale(${zoom})`,
    transformOrigin: `${fx * 100}% ${fy * 100}%`,
  };

  return (
    <div
      onClick={() => onClose()}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 60,
        background: "rgba(0,0,0,0.65)",
        display: "grid",
        placeItems: "center",
        padding: 16,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "min(560px, 100%)",
          background: "var(--card, #141414)",
          border: "1px solid var(--line, rgba(255,255,255,0.12))",
          borderRadius: 16,
          padding: 16,
        }}
      >
        <div className="spread" style={{ marginBottom: 10 }}>
          <b>Crop &amp; focal point</b>
          <button
            type="button"
            onClick={() => onClose()}
            style={{ background: "none", border: "none", color: "var(--text-3)", cursor: "pointer" }}
          >
            ✕
          </button>
        </div>

        <div
          style={{
            aspectRatio: "16 / 9",
            overflow: "hidden",
            borderRadius: 10,
            background: "#000",
            display: "grid",
            placeItems: "center",
          }}
        >
          {clip.url ? (
            clip.type === "video" ? (
              <video src={clip.url} muted loop autoPlay playsInline style={style} />
            ) : (
              <img src={clip.url} alt={clip.name} style={style} />
            )
          ) : (
            <span className="faint" style={{ fontSize: "0.8rem" }}>
              Preview unavailable for this clip
            </span>
          )}
        </div>

        <div style={{ display: "grid", gap: 10, marginTop: 12 }}>
          <label style={{ display: "grid", gap: 4 }}>
            <span className="faint" style={{ fontSize: "0.78rem" }}>
              Horizontal focus
            </span>
            <input type="range" min={0} max={1} step={0.01} value={fx} onChange={(e) => setFx(Number(e.target.value))} />
          </label>
          <label style={{ display: "grid", gap: 4 }}>
            <span className="faint" style={{ fontSize: "0.78rem" }}>
              Vertical focus
            </span>
            <input type="range" min={0} max={1} step={0.01} value={fy} onChange={(e) => setFy(Number(e.target.value))} />
          </label>
          <label style={{ display: "grid", gap: 4 }}>
            <span className="faint" style={{ fontSize: "0.78rem" }}>
              Zoom · {zoom.toFixed(2)}×
            </span>
            <input type="range" min={1} max={3} step={0.05} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} />
          </label>
        </div>

        <div className="spread" style={{ marginTop: 14 }}>
          <Button
            onClick={() => {
              setFx(0.5);
              setFy(0.5);
              setZoom(1);
            }}
          >
            Reset
          </Button>
          <Button variant="primary" onClick={() => onClose({ ...clip, focusX: fx, focusY: fy, zoom })}>
            Done
          </Button>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Scene media editor                                                  */
/* ------------------------------------------------------------------ */

function SceneMediaEditor({
  media,
  onChange,
}: {
  media: EditableClip[];
  onChange: (media: EditableClip[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [cropping, setCropping] = useState<number | null>(null);

  function update(index: number, patch: Partial<EditableClip>) {
    onChange(media.map((m, i) => (i === index ? { ...m, ...patch } : m)));
  }

  function move(index: number, dir: -1 | 1) {
    const j = index + dir;
    if (j < 0 || j >= media.length) return;
    const next = [...media];
    [next[index], next[j]] = [next[j], next[index]];
    onChange(next);
  }

  async function handleFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files || []);
    e.target.value = "";
    if (!files.length) return;
    setBusy(true);
    try {
      const added: EditableClip[] = [];
      for (const file of files) {
        const res = await api.uploadFile(file);
        for (const item of res.items) {
          let url = "";
          try {
            url = (await api.preview(item.storagePath)).url;
          } catch {
            /* thumbnail will fall back to an icon */
          }
          added.push({
            path: item.storagePath,
            name: item.name,
            type: item.type,
            url,
            duration: item.duration ? Math.round(item.duration * 10) / 10 : DEFAULT_CLIP_SECONDS,
          });
        }
      }
      onChange([...media, ...added]);
    } catch (err) {
      toast((err as Error).message || "Upload failed.", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ marginTop: 8 }}>
      <input
        ref={inputRef}
        type="file"
        accept="image/*,video/*"
        multiple
        style={{ display: "none" }}
        onChange={handleFiles}
      />

      <div style={{ display: "grid", gap: 8 }}>
        {media.map((clip, index) => (
          <div
            key={`${clip.path}-${index}`}
            style={{
              display: "grid",
              gridTemplateColumns: "72px minmax(0, 1fr) auto",
              gap: 10,
              alignItems: "center",
              padding: 8,
              borderRadius: 10,
              border: "1px solid var(--line, rgba(255,255,255,0.12))",
              background: "rgba(255,255,255,0.02)",
            }}
          >
            <div
              style={{
                width: 72,
                aspectRatio: "16 / 9",
                borderRadius: 8,
                overflow: "hidden",
                background: "#000",
                display: "grid",
                placeItems: "center",
              }}
            >
              {clip.url ? (
                clip.type === "video" ? (
                  <video src={clip.url} muted playsInline preload="metadata" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                ) : (
                  <img src={clip.url} alt={clip.name} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
                )
              ) : (
                <span style={{ fontSize: "0.9rem" }}>{clip.type === "video" ? "🎬" : "🖼"}</span>
              )}
            </div>

            <div style={{ minWidth: 0, display: "grid", gap: 6 }}>
              <div
                style={{
                  fontSize: "0.8rem",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
                title={clip.name}
              >
                {index + 1}. {clip.name}
              </div>
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: "0.76rem" }} className="faint">
                  Secs
                  <input
                    className="input"
                    type="number"
                    min={0.5}
                    max={MAX_CLIP_SECONDS}
                    step={0.5}
                    value={Number(clip.duration ?? DEFAULT_CLIP_SECONDS)}
                    onChange={(e) => update(index, { duration: Math.max(0.5, Number(e.target.value) || 0.5) })}
                    style={{ width: 74, padding: "4px 6px" }}
                  />
                </label>
                {clip.type === "video" && (
                  <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: "0.76rem", minWidth: 150, flex: 1 }} className="faint">
                    Trim
                    <input
                      type="range"
                      min={0}
                      max={30}
                      step={0.1}
                      value={clip.trimStart ?? 0}
                      onChange={(e) => update(index, { trimStart: Number(e.target.value) })}
                      style={{ flex: 1 }}
                    />
                    <span>{(clip.trimStart ?? 0).toFixed(1)}s</span>
                  </label>
                )}
              </div>
            </div>

            <div style={{ display: "flex", gap: 2 }}>
              <button type="button" style={iconBtn} title="Crop / focal point" onClick={() => setCropping(index)}>
                ⛶
              </button>
              <button type="button" style={iconBtn} title="Move earlier" disabled={index === 0} onClick={() => move(index, -1)}>
                ←
              </button>
              <button type="button" style={iconBtn} title="Move later" disabled={index === media.length - 1} onClick={() => move(index, 1)}>
                →
              </button>
              <button
                type="button"
                style={iconBtn}
                title="Remove clip"
                onClick={() => onChange(media.filter((_, i) => i !== index))}
              >
                ✕
              </button>
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <Button onClick={() => inputRef.current?.click()} loading={busy}>
          ＋ Upload
        </Button>
        <Button onClick={() => setPickerOpen(true)}>🗂 Library</Button>
      </div>

      <AssetPicker
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        onPick={(item) => onChange([...media, libraryToClip(item)])}
        title="Add media to scene"
      />

      {cropping !== null && media[cropping] && (
        <FocalCropModal
          clip={media[cropping]}
          onClose={(updated) => {
            if (updated) update(cropping, { focusX: updated.focusX, focusY: updated.focusY, zoom: updated.zoom });
            setCropping(null);
          }}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Timeline                                                            */
/* ------------------------------------------------------------------ */

function Timeline({
  scenes,
  activeId,
  onSelect,
}: {
  scenes: SceneDraft[];
  activeId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div style={{ overflowX: "auto", paddingBottom: 4 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "stretch", minHeight: 78 }}>
        {scenes.map((scene, index) => {
          const seconds = sceneSeconds(scene);
          const width = Math.max(88, Math.min(280, seconds * 20));
          const active = scene.id === activeId;
          return (
            <button
              key={scene.id}
              type="button"
              onClick={() => onSelect(scene.id)}
              title={`Scene ${index + 1} · ${mmss(seconds)}`}
              style={{
                width,
                flexShrink: 0,
                textAlign: "left",
                padding: 8,
                borderRadius: 10,
                cursor: "pointer",
                color: "inherit",
                border: `1px solid ${active ? "var(--accent, #6ea8fe)" : "var(--line, rgba(255,255,255,0.12))"}`,
                background: active ? "rgba(110,168,254,0.12)" : "rgba(255,255,255,0.03)",
                display: "grid",
                gap: 6,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.74rem", fontWeight: 700 }}>
                <span>Scene {index + 1}</span>
                <span className="faint">{mmss(seconds)}</span>
              </div>
              <div style={{ display: "flex", gap: 2, overflow: "hidden", height: 40 }}>
                {scene.media.slice(0, 3).map((clip, i) =>
                  clip.url ? (
                    clip.type === "video" ? (
                      <video key={i} src={clip.url} muted playsInline preload="metadata" style={{ width: 44, height: "100%", objectFit: "cover", borderRadius: 4 }} />
                    ) : (
                      <img key={i} src={clip.url} alt="" style={{ width: 44, height: "100%", objectFit: "cover", borderRadius: 4 }} />
                    )
                  ) : (
                    <div key={i} style={{ width: 44, height: "100%", borderRadius: 4, background: "rgba(255,255,255,0.08)", display: "grid", placeItems: "center", fontSize: "0.7rem" }}>
                      {clip.type === "video" ? "🎬" : "🖼"}
                    </div>
                  )
                )}
              </div>
              <div className="faint" style={{ fontSize: "0.68rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {scene.script.trim() || "No script"} · {scene.media.length} clip{scene.media.length === 1 ? "" : "s"}
              </div>
            </button>
          );
        })}

        {!scenes.length && <span className="faint" style={{ fontSize: "0.8rem" }}>Add a scene to start the timeline.</span>}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Editor page                                                         */
/* ------------------------------------------------------------------ */

interface CatalogState {
  voices: string[];
  music: string[];
  pacing: string[];
  hooks: string[];
  captionStyles: string[];
  captionPositions: string[];
  transitions: string[];
  minRate: number;
  maxRate: number;
}

export default function Editor() {
  const { jobId = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const { pageName } = useAuth();
  const { job } = useJob(jobId);
  const cloneVoices = useCloneVoices();

  const [catalog, setCatalog] = useState<CatalogState | null>(null);
  const [loadError, setLoadError] = useState("");
  const [settings, setSettings] = useState<VideoSettings | null>(null);
  const [title, setTitle] = useState("");
  const [scenes, setScenes] = useState<SceneDraft[]>([]);
  const [characters, setCharacters] = useState<CharacterDraft[]>([]);
  const [activeId, setActiveId] = useState("");
  const [isPreview, setIsPreview] = useState(false);
  const [showLook, setShowLook] = useState(Boolean(jobId));
  const [submitting, setSubmitting] = useState(false);
  const hydratedFor = useRef("");

  useEffect(() => {
    let alive = true;
    Promise.allSettled([api.catalog(), api.catalogVoices(), api.catalogMusic()]).then((settled) => {
      if (!alive) return;
      const presets = settled[0].status === "fulfilled" ? settled[0].value : null;
      const voices = settled[1].status === "fulfilled" ? settled[1].value : null;
      const music = settled[2].status === "fulfilled" ? settled[2].value : null;
      if (!presets && !voices && !music) {
        setLoadError("Could not reach the Vidyra server. Check that the backend is running and refresh to retry.");
        return;
      }
      setCatalog({
        voices: voices?.voices ?? [],
        music: music?.music ?? [],
        pacing: presets?.pacing ?? ["Breaking News", "Normal Sports Story", "High Energy", "Analysis"],
        hooks: presets?.hooks ?? ["Breaking News", "Question", "Bold Statement", "Debate", "Game Day"],
        captionStyles: presets?.caption_styles ?? ["Karaoke", "Clean", "Bold"],
        captionPositions: presets?.caption_positions ?? ["Top", "Center", "Bottom"],
        transitions: presets?.transitions ?? ["Auto", "fade", "slideleft", "slideright", "wipeleft", "wiperight"],
        minRate: presets?.speech_rate?.min ?? 0.85,
        maxRate: presets?.speech_rate?.max ?? 1.15,
      });
    });
    return () => {
      alive = false;
    };
  }, []);

  // Fresh project defaults once the catalog arrives.
  useEffect(() => {
    if (!catalog || settings) return;
    const id = nextId();
    setSettings({ ...DEFAULT_SETTINGS, voice: catalog.voices[0] || "" });
    setScenes([{ id, script: "", voice: "", transition: "", media: [] }]);
    setActiveId(id);
  }, [catalog, settings]);

  // Hydrate from an existing job (Edit this video → /editor/:jobId).
  useEffect(() => {
    if (!job) return;
    if (hydratedFor.current === job.jobId) return;
    hydratedFor.current = job.jobId;
    let alive = true;

    setTitle(job.title || "");
    setShowLook(true);
    setSettings((prev) => {
      const next = settingsFrom((job.settings || {}) as Record<string, unknown>);
      return { ...next, voice: next.voice || prev?.voice || "" };
    });

    const raw: StoryboardSceneInput[] = job.scenes?.length
      ? job.scenes
      : [
          {
            script: job.script || "",
            voice: "",
            transition: "",
            media: (job.media || []).map((m) => (typeof m === "string" ? { path: m } : m)),
          },
        ];

    Promise.all(
      raw.map(async (scene) => {
        const media = await Promise.all(
          (scene.media || []).map(async (m) => {
            let url = "";
            try {
              url = (await api.preview(m.path)).url;
            } catch {
              /* no thumbnail */
            }
            const kind = typeOf(m.path);
            return {
              path: m.path,
              name: nameFromPath(m.path),
              type: kind,
              url,
              duration: m.duration ?? (scene.duration && scene.media.length ? scene.duration / scene.media.length : DEFAULT_CLIP_SECONDS),
              trimStart: m.trimStart ?? undefined,
              focusX: m.focusX ?? undefined,
              focusY: m.focusY ?? undefined,
              zoom: m.zoom ?? undefined,
            } as EditableClip;
          })
        );
        return {
          id: nextId(),
          script: scene.script || "",
          voice: scene.voice || "",
          transition: scene.transition || "",
          media,
        } as SceneDraft;
      })
    ).then((list) => {
      if (!alive || !list.length) return;
      setScenes(list);
      setActiveId(list[0].id);
    });

    setCharacters(
      (job.characters || []).map((c) => ({ id: nextId(), name: c.name, voice: c.voice }))
    );

    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job]);

  // Keep the default voice valid against the catalog.
  useEffect(() => {
    if (!catalog || !settings) return;
    if (settings.voice.startsWith("clone:")) return;
    if (settings.voice && catalog.voices.includes(settings.voice)) return;
    if (!catalog.voices.length) return;
    setSettings((prev) => (prev ? { ...prev, voice: catalog.voices[0] } : prev));
  }, [catalog, settings?.voice, settings]);

  const totalSeconds = useMemo(() => scenes.reduce((n, s) => n + sceneSeconds(s), 0), [scenes]);
  const totalMedia = useMemo(() => scenes.reduce((n, s) => n + s.media.length, 0), [scenes]);

  function patch(p: Partial<VideoSettings>) {
    setSettings((prev) => (prev ? { ...prev, ...p } : prev));
  }

  function updateScene(id: string, patch: Partial<SceneDraft>) {
    setScenes((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  }

  function addScene() {
    const id = nextId();
    setScenes((prev) => [...prev, { id, script: "", voice: "", transition: "", media: [] }]);
    setActiveId(id);
    requestAnimationFrame(() => document.getElementById(`scene-card-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  function removeScene(id: string) {
    setScenes((prev) => {
      if (prev.length <= 1) return prev;
      const next = prev.filter((s) => s.id !== id);
      if (id === activeId) setActiveId(next[0]?.id || "");
      return next;
    });
  }

  function moveScene(id: string, dir: -1 | 1) {
    setScenes((prev) => {
      const i = prev.findIndex((s) => s.id === id);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const copy = [...prev];
      [copy[i], copy[j]] = [copy[j], copy[i]];
      return copy;
    });
  }

  function splitScene(id: string) {
    setScenes((prev) => {
      const index = prev.findIndex((s) => s.id === id);
      const scene = prev[index];
      if (!scene) return prev;

      const half = Math.ceil(scene.media.length / 2);
      const leftMedia = scene.media.slice(0, half);
      const rightMedia = scene.media.slice(half);

      const sentences = scene.script.match(/[^.!?\n]+[.!?]?/g) || [];
      let leftScript = scene.script;
      let rightScript = "";
      if (sentences.length > 1) {
        const cut = Math.ceil(sentences.length / 2);
        leftScript = sentences.slice(0, cut).join(" ").trim();
        rightScript = sentences.slice(cut).join(" ").trim();
      } else if (scene.script.trim()) {
        const words = scene.script.trim().split(/\s+/);
        const cut = Math.ceil(words.length / 2);
        leftScript = words.slice(0, cut).join(" ");
        rightScript = words.slice(cut).join(" ");
      }

      // A single clip can't be divided by index, so give the new scene its own
      // copy of the clip to trim independently.
      const rightClips = rightMedia.length ? rightMedia : leftMedia.slice(-1).map((c) => ({ ...c }));
      const rightId = nextId();
      const left: SceneDraft = { ...scene, script: leftScript, media: leftMedia };
      const right: SceneDraft = { ...scene, id: rightId, script: rightScript, transition: "", media: rightClips };
      setActiveId(rightId);
      return [...prev.slice(0, index), left, right, ...prev.slice(index + 1)];
    });
  }

  function selectScene(id: string) {
    setActiveId(id);
    document.getElementById(`scene-card-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function addCharacter() {
    setCharacters((prev) => [...prev, { id: nextId(), name: "", voice: settings?.voice || "" }]);
  }

  function updateCharacter(id: string, patch: Partial<CharacterDraft>) {
    setCharacters((prev) => prev.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  }

  async function run(preview: boolean) {
    if (!settings) return;

    const usable = scenes.filter((s) => s.script.trim() || s.media.length);
    if (!usable.length) {
      toast("Add at least one scene with a script and media.", "error");
      return;
    }
    const bad = usable.find((s) => !s.script.trim() || !s.media.length);
    if (bad) {
      toast("Every scene needs both a script and at least one media file.", "error");
      return;
    }

    const cleanChars = characters
      .filter((c) => c.name.trim())
      .map((c) => ({ name: c.name.trim(), voice: c.voice || settings.voice }));

    setIsPreview(preview);
    setSubmitting(true);
    try {
      const res = await api.createJob({
        title: title.trim() || (preview ? "Timeline preview" : "Timeline video"),
        script: "",
        media: [],
        settings: {
          ...settings,
          music: settings.music || null,
          preview,
        },
        scenes: usable.map((s) => ({
          script: s.script.trim(),
          voice: s.voice || undefined,
          transition: s.transition || undefined,
          duration: Math.round(sceneSeconds(s) * 10) / 10,
          media: s.media.map((m) => ({
            path: m.path,
            duration: Math.round(clipSeconds(m) * 10) / 10,
            trimStart: m.type === "video" ? m.trimStart ?? 0 : undefined,
            focusX: m.focusX ?? 0.5,
            focusY: m.focusY ?? 0.5,
            zoom: m.zoom ?? 1,
          })),
        })),
        characters: cleanChars,
      });
      toast(preview ? "Preview queued — it stays out of your library." : "Render queued.", "success");
      navigate(`/job/${res.job_id}`);
    } catch (e) {
      toast((e as Error).message || "Failed to start render.", "error");
    } finally {
      setSubmitting(false);
      setIsPreview(false);
    }
  }

  if (loadError) {
    return (
      <Card>
        <p style={{ margin: 0 }}>{loadError}</p>
      </Card>
    );
  }

  if (!catalog || !settings) {
    return (
      <div style={{ display: "grid", placeItems: "center", minHeight: "40dvh" }}>
        <Spinner />
      </div>
    );
  }

  return (
    <div className="fade-up" style={{ paddingBottom: 28 }}>
      <header className="spread" style={{ marginBottom: 16, gap: 12, flexWrap: "wrap" }}>
        <div style={{ minWidth: 220 }}>
          <h2 style={{ margin: 0 }}>Editor</h2>
          <p className="faint" style={{ fontSize: "0.86rem", margin: "2px 0 0" }}>
            Timeline-first editing. Trim, split, reorder and crop, then render.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Button onClick={() => run(true)} loading={isPreview} disabled={submitting}>
            Preview
          </Button>
          <Button variant="primary" onClick={() => run(false)} loading={submitting && !isPreview} disabled={submitting}>
            Render &amp; export
          </Button>
        </div>
      </header>

      <div style={{ display: "grid", gap: 16 }}>
        <Card>
          <div style={{ display: "grid", gap: 12, gridTemplateColumns: "minmax(0, 1fr)" }}>
            <Field label="Title">
              <input
                className="input"
                placeholder="My video"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </Field>

            <div>
              <div className="spread" style={{ marginBottom: 8 }}>
                <div style={{ fontWeight: 700 }}>Timeline</div>
                <span className="faint" style={{ fontSize: "0.8rem" }}>
                  {scenes.length} scene{scenes.length === 1 ? "" : "s"} · {totalMedia} clip{totalMedia === 1 ? "" : "s"} ·{" "}
                  {mmss(totalSeconds)}
                </span>
              </div>
              <Timeline scenes={scenes} activeId={activeId} onSelect={selectScene} />
            </div>
          </div>
        </Card>

        <Card>
          <div className="spread">
            <div>
              <div style={{ fontWeight: 700 }}>Look &amp; sound</div>
              <div className="faint" style={{ fontSize: "0.8rem" }}>
                Voice, pacing, captions, music, title card and output shape.
              </div>
            </div>
            <Button onClick={() => setShowLook((v) => !v)}>{showLook ? "Hide" : "Show"}</Button>
          </div>

          {showLook && (
            <div style={{ display: "grid", gap: 18, marginTop: 14 }}>
              <StepSettings
                settings={settings}
                onUpdate={patch}
                voices={catalog.voices}
                pacing={catalog.pacing}
                hooks={catalog.hooks}
                minRate={catalog.minRate}
                maxRate={catalog.maxRate}
                cloneVoices={cloneVoices}
              />
              <StepVisuals
                settings={settings}
                onUpdate={patch}
                captionStyles={catalog.captionStyles}
                captionPositions={catalog.captionPositions}
                transitions={catalog.transitions}
                music={catalog.music}
                pageName={pageName}
              />
            </div>
          )}
        </Card>

        <Card>
          <div className="spread" style={{ marginBottom: 8 }}>
            <div>
              <div style={{ fontWeight: 700 }}>Characters</div>
              <div className="faint" style={{ fontSize: "0.8rem" }}>
                Write <code>Name: text</code> in a scene to speak it in that character's voice.
              </div>
            </div>
            <Button onClick={addCharacter}>＋ Character</Button>
          </div>

          {characters.length === 0 ? (
            <div className="faint" style={{ fontSize: "0.84rem" }}>
              No characters yet — single-voice narration.
            </div>
          ) : (
            <div className="stack-v">
              {characters.map((c) => (
                <div key={c.id} style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8 }}>
                  <input
                    className="input"
                    placeholder="Name (e.g. Ada)"
                    value={c.name}
                    onChange={(e) => updateCharacter(c.id, { name: e.target.value })}
                  />
                  <VoiceSelect
                    value={c.voice}
                    onChange={(voice) => updateCharacter(c.id, { voice })}
                    voices={catalog.voices}
                    cloneVoices={cloneVoices}
                  />
                  <button
                    type="button"
                    onClick={() => setCharacters((prev) => prev.filter((x) => x.id !== c.id))}
                    style={{ background: "none", border: "none", color: "var(--text-3)", cursor: "pointer" }}
                    title="Remove character"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
        </Card>

        <div>
          <div className="spread" style={{ marginBottom: 10 }}>
            <h3 className="section-title" style={{ margin: 0 }}>
              Scenes ({scenes.length})
            </h3>
            <Button onClick={addScene}>＋ Scene</Button>
          </div>

          {scenes.length === 0 ? (
            <EmptyState icon="🎬" title="No scenes yet" hint="Add a scene to begin." />
          ) : (
            <div className="stack-v">
              {scenes.map((scene, index) => (
                <div key={scene.id} id={`scene-card-${scene.id}`}>
                  <Card>
                    <div className="spread" style={{ marginBottom: 8 }}>
                      <div style={{ fontWeight: 700 }}>
                        Scene {index + 1}{" "}
                        <span className="faint" style={{ fontWeight: 400, fontSize: "0.8rem" }}>
                          · {mmss(sceneSeconds(scene))}
                        </span>
                      </div>
                      <div style={{ display: "flex", gap: 4 }}>
                        <Button onClick={() => splitScene(scene.id)} title="Split this scene in two">
                          Split
                        </Button>
                        <button type="button" onClick={() => moveScene(scene.id, -1)} disabled={index === 0} style={iconBtn} title="Move up">
                          ↑
                        </button>
                        <button
                          type="button"
                          onClick={() => moveScene(scene.id, 1)}
                          disabled={index === scenes.length - 1}
                          style={iconBtn}
                          title="Move down"
                        >
                          ↓
                        </button>
                        <button
                          type="button"
                          onClick={() => removeScene(scene.id)}
                          disabled={scenes.length <= 1}
                          style={iconBtn}
                          title="Delete scene"
                        >
                          ✕
                        </button>
                      </div>
                    </div>

                    <Field label="Script">
                      <textarea
                        className="textarea"
                        rows={3}
                        placeholder={characters.length ? "Narration…\nAda: Your line here." : "What should be said in this scene…"}
                        value={scene.script}
                        onChange={(e) => updateScene(scene.id, { script: e.target.value })}
                      />
                    </Field>

                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginTop: 6 }}>
                      <Field label="Voice">
                        <VoiceSelect
                          value={scene.voice}
                          onChange={(voice) => updateScene(scene.id, { voice })}
                          voices={catalog.voices}
                          cloneVoices={cloneVoices}
                          inheritLabel="Use default"
                        />
                      </Field>
                      <Field label="Transition">
                        <select
                          className="input"
                          value={scene.transition}
                          onChange={(e) => updateScene(scene.id, { transition: e.target.value })}
                        >
                          <option value="">Use default</option>
                          {catalog.transitions.map((t) => (
                            <option key={t} value={t}>
                              {t}
                            </option>
                          ))}
                        </select>
                      </Field>
                    </div>

                    <div className="field-label" style={{ marginTop: 6 }}>
                      Media
                    </div>
                    <SceneMediaEditor
                      media={scene.media}
                      onChange={(media) => updateScene(scene.id, { media })}
                    />
                  </Card>
                </div>
              ))}
            </div>
          )}
        </div>

        <div>
          <Button variant="primary" size="lg" loading={submitting && !isPreview} disabled={submitting} onClick={() => run(false)}>
            Render &amp; export
          </Button>
        </div>
      </div>
    </div>
  );
}

const iconBtn: React.CSSProperties = {
  width: 28,
  height: 28,
  borderRadius: 8,
  border: "1px solid var(--line, rgba(255,255,255,0.12))",
  background: "rgba(255,255,255,0.04)",
  color: "var(--text-2)",
  cursor: "pointer",
};
