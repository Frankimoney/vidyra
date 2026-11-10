import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../services/api";
import { useJob } from "../hooks/useJobs";
import { useAuth } from "../contexts/AuthContext";
import { useCloneVoices } from "../hooks/useCloneVoices";
import { useToast } from "../components/feedback";
import { Button, Card, EmptyState, Field, Segmented, Spinner } from "../components/ui";
import VoiceSelect from "../components/VoiceSelect";
import { AssetPicker } from "../components/AssetPicker";
import { DEFAULT_SETTINGS, settingsFrom } from "../utils/settings";
import StepSettings from "../components/create/StepSettings";
import StepVisuals from "../components/create/StepVisuals";
import type {
  EditableClip,
  LibraryItem,
  PreloadMedia,
  RecipeHandoff,
  StoryboardSceneInput,
  TimelineAudio,
  TimelineOverlay,
  TimelineText,
  TimelineTracks,
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
  /** Recipe drafts: the AI visual prompt this scene is generated from. */
  setting?: string;
  shot?: string;
  /** A creator-selected Studio take used instead of generating this scene. */
  referenceImage?: string;
  takes?: { id: string; path: string; url: string; setting: string }[];
}

interface CharacterDraft {
  id: string;
  name: string;
  voice: string;
  description: string;
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

/** Timeline length: media duration, or an estimate for narration-only AI scenes. */
function scenePlaySeconds(scene: SceneDraft): number {
  const media = sceneSeconds(scene);
  if (media > 0.1) return media;
  const words = scene.script.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(2, words / 2.6);
}

const EMPTY_TIMELINE = (): TimelineTracks => ({
  texts: [],
  overlays: [],
  audio: [],
});

function normalizeTimeline(raw: unknown): TimelineTracks {
  const value = (raw || {}) as Partial<TimelineTracks>;
  return {
    texts: Array.isArray(value.texts) ? value.texts : [],
    overlays: Array.isArray(value.overlays) ? value.overlays : [],
    audio: Array.isArray(value.audio) ? value.audio : [],
  };
}

function mmss(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function clipLook(clip: EditableClip, elapsed: number) {
  const kfs = [...(clip.keyframes || [])].sort((a, b) => a.t - b.t);
  if (kfs.length < 2) {
    return {
      zoom: clip.zoom ?? 1,
      fx: clip.focusX ?? 0.5,
      fy: clip.focusY ?? 0.5,
    };
  }
  const progress = Math.max(0, Math.min(1, elapsed / Math.max(0.1, clipSeconds(clip))));
  let left = kfs[0];
  let right = kfs[kfs.length - 1];
  for (let i = 0; i < kfs.length - 1; i += 1) {
    if (progress >= kfs[i].t && progress <= kfs[i + 1].t) {
      left = kfs[i];
      right = kfs[i + 1];
      break;
    }
  }
  const span = Math.max(0.0001, right.t - left.t);
  const f = Math.max(0, Math.min(1, (progress - left.t) / span));
  return {
    zoom: left.zoom + (right.zoom - left.zoom) * f,
    fx: left.focusX + (right.focusX - left.focusX) * f,
    fy: left.focusY + (right.focusY - left.focusY) * f,
  };
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
              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", fontSize: "0.76rem" }} className="faint">
                {clip.type === "video" && (
                  <>
                    <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      Speed
                      <select
                        className="input"
                        value={clip.speed ?? 1}
                        onChange={(e) => update(index, { speed: Number(e.target.value) })}
                        style={{ padding: "3px 5px", width: 72 }}
                      >
                        {[0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4].map((v) => (
                          <option key={v} value={v}>{v}×</option>
                        ))}
                      </select>
                    </label>
                    <label style={{ display: "flex", gap: 4, alignItems: "center" }}>
                      <input type="checkbox" checked={!!clip.reverse} onChange={(e) => update(index, { reverse: e.target.checked })} /> Reverse
                    </label>
                    <label style={{ display: "flex", gap: 4, alignItems: "center" }}>
                      <input type="checkbox" checked={!!clip.freeze} onChange={(e) => update(index, { freeze: e.target.checked })} /> Freeze
                    </label>
                  </>
                )}
                <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  Motion
                  <select
                    className="input"
                    value={clip.keyframes && clip.keyframes.length >= 2 ? "keyframes" : clip.motion ?? "auto"}
                    onChange={(e) => {
                      if (e.target.value === "keyframes") {
                        update(index, {
                          keyframes: [
                            { t: 0, zoom: clip.zoom ?? 1, focusX: clip.focusX ?? 0.5, focusY: clip.focusY ?? 0.5 },
                            { t: 1, zoom: Math.min(3, (clip.zoom ?? 1) + 0.15), focusX: clip.focusX ?? 0.5, focusY: clip.focusY ?? 0.5 },
                          ],
                        });
                      } else {
                        update(index, { keyframes: [], motion: e.target.value });
                      }
                    }}
                    style={{ padding: "3px 5px", width: 120 }}
                  >
                    <option value="auto">Auto</option>
                    <option value="none">None</option>
                    <option value="zoomIn">Zoom in</option>
                    <option value="zoomOut">Zoom out</option>
                    <option value="panLeft">Pan left</option>
                    <option value="panRight">Pan right</option>
                    {clip.type === "image" && <option value="keyframes">Keyframes</option>}
                  </select>
                </label>
              </div>
              {clip.type === "image" && clip.keyframes && clip.keyframes.length >= 2 && (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 6, fontSize: "0.72rem" }} className="faint">
                  {[0, clip.keyframes.length - 1].map((kfIndex) => {
                    const kf = clip.keyframes![kfIndex];
                    const label = kfIndex === 0 ? "Start" : "End";
                    return (
                      <div key={kfIndex} style={{ display: "grid", gap: 3 }}>
                        <b>{label}</b>
                        <label style={{ display: "flex", gap: 4, alignItems: "center" }}>
                          Zoom
                          <input className="input" type="number" min={1} max={3} step={0.05} value={kf.zoom}
                            onChange={(e) => {
                              const next = clip.keyframes!.map((k, i) => i === kfIndex ? { ...k, zoom: Number(e.target.value) } : k);
                              update(index, { keyframes: next });
                            }} style={{ width: 64, padding: "2px 4px" }} />
                        </label>
                        <label style={{ display: "flex", gap: 4, alignItems: "center" }}>
                          X
                          <input className="input" type="number" min={0} max={1} step={0.05} value={kf.focusX}
                            onChange={(e) => {
                              const next = clip.keyframes!.map((k, i) => i === kfIndex ? { ...k, focusX: Number(e.target.value) } : k);
                              update(index, { keyframes: next });
                            }} style={{ width: 64, padding: "2px 4px" }} />
                        </label>
                        <label style={{ display: "flex", gap: 4, alignItems: "center" }}>
                          Y
                          <input className="input" type="number" min={0} max={1} step={0.05} value={kf.focusY}
                            onChange={(e) => {
                              const next = clip.keyframes!.map((k, i) => i === kfIndex ? { ...k, focusY: Number(e.target.value) } : k);
                              update(index, { keyframes: next });
                            }} style={{ width: 64, padding: "2px 4px" }} />
                        </label>
                      </div>
                    );
                  })}
                </div>
              )}
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
  zoom,
  onSelect,
  onReorder,
}: {
  scenes: SceneDraft[];
  activeId: string;
  zoom: number;
  onSelect: (id: string) => void;
  onReorder: (fromId: string, toId: string) => void;
}) {
  return (
    <div style={{ overflowX: "auto", paddingBottom: 4 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "stretch", minHeight: 78 }}>
        {scenes.map((scene, index) => {
          const seconds = scenePlaySeconds(scene);
          const width = Math.max(70, Math.min(600, seconds * 20 * zoom));
          const active = scene.id === activeId;
          return (
            <button
              key={scene.id}
              type="button"
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData("text/plain", scene.id);
                e.dataTransfer.effectAllowed = "move";
              }}
              onDragOver={(e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
              }}
              onDrop={(e) => {
                e.preventDefault();
                const from = e.dataTransfer.getData("text/plain");
                if (from && from !== scene.id) onReorder(from, scene.id);
              }}
              onClick={() => onSelect(scene.id)}
              title={`Scene ${index + 1} · ${mmss(seconds)} — drag to reorder`}
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
/* Browser timeline preview (no GPU)                                   */
/* ------------------------------------------------------------------ */

function TimelinePreview({
  scenes,
  timeline,
  aspect,
  total,
  time,
  playing,
  onTime,
  onPlaying,
}: {
  scenes: SceneDraft[];
  timeline: TimelineTracks;
  aspect?: string;
  total: number;
  time: number;
  playing: boolean;
  onTime: (value: number) => void;
  onPlaying: (value: boolean) => void;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const audioRefs = useRef<Record<string, HTMLAudioElement | null>>({});

  let sceneStart = 0;
  let activeScene: SceneDraft | null = null;
  let activeClip: EditableClip | null = null;
  let clipStart = 0;
  for (const scene of scenes) {
    const sceneDuration = scenePlaySeconds(scene);
    if (!activeScene && (time < sceneStart + sceneDuration || scene === scenes[scenes.length - 1])) {
      activeScene = scene;
      let cursor = sceneStart;
      for (const clip of scene.media) {
        const duration = clipSeconds(clip);
        if (time < cursor + duration || clip === scene.media[scene.media.length - 1]) {
          activeClip = clip;
          clipStart = cursor;
          break;
        }
        cursor += duration;
      }
    }
    sceneStart += sceneDuration;
  }

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !activeClip) return;
    const desired = Math.max(0, time - clipStart);
    if (Math.abs(video.currentTime - desired) > 0.35) {
      try {
        video.currentTime = desired;
      } catch {
        /* metadata not ready */
      }
    }
    if (playing) video.play().catch(() => undefined);
    else video.pause();
  }, [time, playing, activeClip?.path, clipStart, activeClip]);

  useEffect(() => {
    for (const track of timeline.audio) {
      const audio = audioRefs.current[track.id];
      if (!audio) continue;
      const active = time >= track.start && time < track.end;
      if (active && playing) {
        const desired = time - track.start;
        if (Math.abs(audio.currentTime - desired) > 0.5) audio.currentTime = desired;
        audio.volume = Math.max(0, Math.min(1, track.volume));
        audio.play().catch(() => undefined);
      } else {
        audio.pause();
      }
    }
  }, [time, playing, timeline.audio]);

  const ratio = aspect && aspect !== "auto" ? aspect.replace(":", " / ") : "9 / 16";
  const activeLook = activeClip ? clipLook(activeClip, time - clipStart) : { zoom: 1, fx: 0.5, fy: 0.5 };
  const activeTexts = timeline.texts.filter((t) => time >= t.start && time <= t.end);
  const activeOverlays = timeline.overlays.filter(
    (o) => time >= o.start && time <= o.end && o.url
  );

  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div
        style={{
          position: "relative",
          aspectRatio: ratio,
          maxHeight: 420,
          width: "100%",
          maxWidth: 340,
          margin: "0 auto",
          background: "#000",
          borderRadius: 12,
          overflow: "hidden",
          containerType: "size",
        }}
      >
        {activeClip ? (
          activeClip.type === "video" ? (
            <video
              key={`${activeClip.path}-${clipStart}`}
              ref={videoRef}
              src={activeClip.url}
              muted
              playsInline
              preload="auto"
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                objectPosition: `${activeLook.fx * 100}% ${activeLook.fy * 100}%`,
                transform: `scale(${activeLook.zoom})`,
              }}
            />
          ) : (
            <img
              src={activeClip.url}
              alt={activeClip.name}
              style={{
                width: "100%",
                height: "100%",
                objectFit: "cover",
                objectPosition: `${activeLook.fx * 100}% ${activeLook.fy * 100}%`,
                transform: `scale(${activeLook.zoom})`,
              }}
            />
          )
        ) : activeScene ? (
          <div
            style={{
              height: "100%",
              display: "grid",
              placeItems: "center",
              padding: 18,
              textAlign: "center",
              background: "linear-gradient(160deg, #1b2330, #0b0d12)",
            }}
          >
            <div>
              <div style={{ fontSize: "1.5rem" }}>🎬</div>
              <div className="faint" style={{ fontSize: "0.78rem", marginTop: 6 }}>
                AI scene — preview after render
              </div>
              <div style={{ fontSize: "0.7rem", marginTop: 6, opacity: 0.8 }}>
                {(activeScene.setting || activeScene.script).slice(0, 140)}
              </div>
            </div>
          </div>
        ) : (
          <div style={{ height: "100%", display: "grid", placeItems: "center" }}>
            <span className="faint">Nothing to preview yet</span>
          </div>
        )}

        {activeOverlays.map((overlay) => (
          <img
            key={overlay.id}
            src={overlay.url}
            alt={overlay.name}
            style={{
              position: "absolute",
              width: `${overlay.width * 100}%`,
              left: `${overlay.x * 100}%`,
              top: `${overlay.y * 100}%`,
              transform: `translate(-${overlay.x * 100}%, -${overlay.y * 100}%)`,
              opacity: overlay.opacity,
              pointerEvents: "none",
            }}
          />
        ))}

        {activeTexts.map((text) => {
          const fade = text.animation === "fade"
            ? Math.max(0, Math.min(1, Math.min((time - text.start) / 0.3, (text.end - time) / 0.3)))
            : 1;
          return (
          <div
            key={text.id}
            style={{
              opacity: fade,
              position: "absolute",
              left: `${text.x * 100}%`,
              top: `${text.y * 100}%`,
              transform: "translate(-50%, -50%)",
              maxWidth: "92%",
              textAlign: "center",
              color: text.color,
              fontSize: `${(text.size / 1920) * 100}cqh`,
              fontWeight: 800,
              lineHeight: 1.15,
              background: text.background ? "rgba(0,0,0,0.55)" : "transparent",
              padding: text.background ? "0.25em 0.45em" : 0,
              borderRadius: 6,
              pointerEvents: "none",
            }}
          >
            {text.text}
          </div>
          );
        })}
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button
          type="button"
          style={iconBtn}
          onClick={() => onPlaying(!playing)}
          title={playing ? "Pause" : "Play"}
        >
          {playing ? "❚❚" : "▶"}
        </button>
        <input
          type="range"
          min={0}
          max={Math.max(0.1, total)}
          step={0.05}
          value={Math.min(time, total)}
          onChange={(e) => onTime(Number(e.target.value))}
          style={{ flex: 1 }}
        />
        <span className="faint" style={{ fontSize: "0.75rem", minWidth: 24 }}>
          {mmss(time)} / {mmss(total)}
        </span>
      </div>

      {timeline.audio.map((track) => (
        <audio
          key={track.id}
          ref={(el) => {
            audioRefs.current[track.id] = el;
          }}
          src={track.url}
          preload="auto"
        />
      ))}
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
  const location = useLocation();
  const navigate = useNavigate();
  const handoff = (location.state as { draft?: RecipeHandoff } | null)?.draft;
  const preload = (location.state as { preload?: PreloadMedia | PreloadMedia[] } | null)
    ?.preload;
  const [sceneMode, setSceneMode] = useState<"ai" | "media">(handoff ? "ai" : "media");
  const aiMode = sceneMode === "ai";
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
  const [timeline, setTimeline] = useState<TimelineTracks>(EMPTY_TIMELINE);
  const [previewTime, setPreviewTime] = useState(0);
  const [previewPlaying, setPreviewPlaying] = useState(false);
  const [overlayPicker, setOverlayPicker] = useState<"image" | "video" | null>(null);
  const [audioPicker, setAudioPicker] = useState(false);
  const [timelineZoom, setTimelineZoom] = useState(1);
  const [takeBusy, setTakeBusy] = useState("");
  const hydratedFor = useRef("");
  const hydratedHandoff = useRef(false);
  const preloadDone = useRef(false);
  const scenesRef = useRef<SceneDraft[]>([]);
  const undoStack = useRef<SceneDraft[][]>([]);
  const redoStack = useRef<SceneDraft[][]>([]);
  const [historyState, setHistoryState] = useState({ past: 0, future: 0 });

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
    if (handoff || !catalog || settings) return;
    const id = nextId();
    setSettings({ ...DEFAULT_SETTINGS, voice: catalog.voices[0] || "" });
    setScenes([{ id, script: "", voice: "", transition: "", media: [] }]);
    setActiveId(id);
  }, [catalog, settings]);

  // Recipe draft → AI scene mode (New Video → Open in Editor).
  useEffect(() => {
    if (!handoff || hydratedHandoff.current) return;
    hydratedHandoff.current = true;
    undoStack.current = [];
    redoStack.current = [];
    setHistoryState({ past: 0, future: 0 });
    setSceneMode("ai");
    setTimeline(EMPTY_TIMELINE());
    const d = handoff.defaults || {};
    setTitle(handoff.outline.title || "AI film");
    setShowLook(true);
    setSettings({
      ...DEFAULT_SETTINGS,
      ...settingsFrom(d),
      voice: String(d.voice || ""),
      page_name: String(handoff.workspace?.page_name || d.page_name || ""),
      aspect: String(d.aspect || "9:16"),
    });
    const list = (handoff.outline.scenes || []).map((sc) => ({
      id: nextId(),
      script: sc.narration || "",
      voice: "",
      transition: sc.transition || "",
      media: [],
      setting: sc.setting || "",
      shot: sc.shot || "",
    }));
    if (list.length) {
      setScenes(list);
      setActiveId(list[0].id);
    }
    setCharacters(
      (handoff.outline.characters || []).map((c) => ({
        id: nextId(),
        name: c.name,
        voice: c.voice || "",
        description: c.description || "",
      }))
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handoff]);

  // Hydrate from an existing job (Edit this video → /editor/:jobId).
  useEffect(() => {
    if (handoff || !job) return;
    if (hydratedFor.current === job.jobId) return;
    hydratedFor.current = job.jobId;
    undoStack.current = [];
    redoStack.current = [];
    setHistoryState({ past: 0, future: 0 });
    let alive = true;

    setTitle(job.title || "");
    setShowLook(true);
    setTimeline(normalizeTimeline(job.timeline));
    setSettings((prev) => {
      const next = settingsFrom((job.settings || {}) as Record<string, unknown>);
      return { ...next, voice: next.voice || prev?.voice || "" };
    });

    // A finished AI film stores its plan in filmPlot, not storyboard scenes.
    const plot = (job as { filmPlot?: { title?: string; scenes?: any[]; characters?: any[] } })
      .filmPlot;
    if (plot?.scenes?.length) {
      setSceneMode("ai");
      setTitle(plot.title || job.title || "");
      const list = plot.scenes.map((sc: any) => {
        const dialogue = Object.entries(sc.dialogue || {}).map(
          ([who, line]) => `${who}: ${line}`
        );
        return {
          id: nextId(),
          script: [sc.narration, ...dialogue].filter(Boolean).join("\n"),
          voice: "",
          transition: sc.transition || "",
          media: [],
          setting: sc.setting || "",
          shot: sc.shot || "",
        } as SceneDraft;
      });
      setScenes(list);
      setActiveId(list[0].id);
      setCharacters(
        (plot.characters || job.characters || []).map((c: any) => ({
          id: nextId(),
          name: c.name,
          voice: c.voice || "",
          description: c.description || c.name,
        }))
      );
      return () => {
        alive = false;
      };
    }

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
              speed: m.speed ?? undefined,
              reverse: m.reverse ?? undefined,
              freeze: m.freeze ?? undefined,
              motion: m.motion ?? undefined,
              keyframes: m.keyframes ?? undefined,
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
      (job.characters || []).map((c) => ({
        id: nextId(),
        name: c.name,
        voice: c.voice,
        description: c.name,
      }))
    );

    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job]);

  // Studio "Use in video" → drop the generated asset into the first scene.
  useEffect(() => {
    if (!preload || preloadDone.current || !settings) return;
    preloadDone.current = true;
    const incoming = Array.isArray(preload) ? preload : [preload];
    const clips: EditableClip[] = incoming.map((p) => ({
      path: p.path,
      name: p.name,
      type: p.kind === "video" ? "video" : "image",
      url: p.url || "",
      duration: p.duration ?? DEFAULT_CLIP_SECONDS,
    }));
    setScenes((prev) => {
      if (!prev.length) {
        const id = nextId();
        setActiveId(id);
        return [{ id, script: "", voice: "", transition: "", media: clips }];
      }
      return prev.map((scene, i) =>
        i === 0 ? { ...scene, media: [...scene.media, ...clips] } : scene
      );
    });
    if (!handoff) {
      navigate(location.pathname, { replace: true, state: null });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preload, settings]);

  // Keep the default voice valid against the catalog.
  useEffect(() => {
    if (!catalog || !settings) return;
    if (settings.voice.startsWith("clone:")) return;
    if (settings.voice && catalog.voices.includes(settings.voice)) return;
    if (!catalog.voices.length) return;
    setSettings((prev) => (prev ? { ...prev, voice: catalog.voices[0] } : prev));
  }, [catalog, settings?.voice, settings]);

  useEffect(() => {
    scenesRef.current = scenes;
  }, [scenes]);

  function syncHistory() {
    setHistoryState({
      past: undoStack.current.length,
      future: redoStack.current.length,
    });
  }

  function applyScenes(next: SceneDraft[]) {
    const prev = scenesRef.current;
    if (next === prev) return;
    undoStack.current.push(prev);
    if (undoStack.current.length > 50) undoStack.current.shift();
    redoStack.current = [];
    scenesRef.current = next;
    setScenes(next);
    syncHistory();
  }

  function commitScenes(updater: (prev: SceneDraft[]) => SceneDraft[]) {
    applyScenes(updater(scenesRef.current));
  }

  function undoScenes() {
    const prev = undoStack.current.pop();
    if (!prev) return;
    redoStack.current.push(scenesRef.current);
    scenesRef.current = prev;
    setScenes(prev);
    if (!prev.some((s) => s.id === activeId)) setActiveId(prev[0]?.id || "");
    syncHistory();
  }

  function redoScenes() {
    const next = redoStack.current.pop();
    if (!next) return;
    undoStack.current.push(scenesRef.current);
    scenesRef.current = next;
    setScenes(next);
    if (!next.some((s) => s.id === activeId)) setActiveId(next[0]?.id || "");
    syncHistory();
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "z") return;
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName?.toLowerCase() || "";
      if (tag === "input" || tag === "textarea" || tag === "select" || el?.isContentEditable) {
        return;
      }
      e.preventDefault();
      if (e.shiftKey) redoScenes();
      else undoScenes();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeId]);

  const totalSeconds = useMemo(
    () => scenes.reduce((n, s) => n + scenePlaySeconds(s), 0),
    [scenes]
  );

  useEffect(() => {
    if (!previewPlaying) return;
    const timer = window.setInterval(() => {
      setPreviewTime((current) => {
        const next = current + 0.05;
        if (next >= totalSeconds) {
          setPreviewPlaying(false);
          return totalSeconds;
        }
        return next;
      });
    }, 50);
    return () => window.clearInterval(timer);
  }, [previewPlaying, totalSeconds]);

  useEffect(() => {
    if (previewTime > totalSeconds) setPreviewTime(totalSeconds);
  }, [totalSeconds, previewTime]);
  const totalMedia = useMemo(() => scenes.reduce((n, s) => n + s.media.length, 0), [scenes]);

  function patch(p: Partial<VideoSettings>) {
    setSettings((prev) => (prev ? { ...prev, ...p } : prev));
  }

  function patchText(id: string, patch: Partial<TimelineText>) {
    setTimeline((prev) => ({
      ...prev,
      texts: prev.texts.map((t) => (t.id === id ? { ...t, ...patch } : t)),
    }));
  }

  function addText() {
    const start = Math.min(previewTime, Math.max(0, totalSeconds - 1));
    setTimeline((prev) => ({
      ...prev,
      texts: [
        ...prev.texts,
        {
          id: nextId(),
          text: "New title",
          start,
          end: Math.min(totalSeconds || start + 3, start + 3),
          x: 0.5,
          y: 0.82,
          size: 54,
          color: "#ffffff",
          background: true,
          animation: "fade",
        },
      ],
    }));
  }

  function patchOverlay(id: string, patch: Partial<TimelineOverlay>) {
    setTimeline((prev) => ({
      ...prev,
      overlays: prev.overlays.map((o) => (o.id === id ? { ...o, ...patch } : o)),
    }));
  }

  function addOverlay(item: LibraryItem) {
    const start = Math.min(previewTime, Math.max(0, totalSeconds - 1));
    setTimeline((prev) => ({
      ...prev,
      overlays: [
        ...prev.overlays,
        {
          id: nextId(),
          path: item.path,
          name: item.name,
          kind: item.kind === "video" ? "video" : "image",
          url: item.url,
          start,
          end: Math.min(totalSeconds || start + 4, start + 4),
          x: 0.5,
          y: 0.5,
          width: 0.3,
          opacity: 1,
        },
      ],
    }));
    setOverlayPicker(null);
  }

  function patchAudio(id: string, patch: Partial<TimelineAudio>) {
    setTimeline((prev) => ({
      ...prev,
      audio: prev.audio.map((a) => (a.id === id ? { ...a, ...patch } : a)),
    }));
  }

  function addAudio(item: LibraryItem) {
    const start = Math.min(previewTime, Math.max(0, totalSeconds - 1));
    setTimeline((prev) => ({
      ...prev,
      audio: [
        ...prev.audio,
        {
          id: nextId(),
          path: item.path,
          name: item.name,
          url: item.url,
          start,
          end: Math.min(totalSeconds || start + 10, start + 10),
          volume: 0.6,
          fadeIn: 0.3,
          fadeOut: 0.3,
        },
      ],
    }));
    setAudioPicker(false);
  }

  function updateScene(id: string, patch: Partial<SceneDraft>) {
    commitScenes((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  }

  async function generateTake(scene: SceneDraft) {
    const prompt = (scene.setting || "").trim();
    if (!prompt) {
      toast("Write a visual prompt first.", "error");
      return;
    }
    setTakeBusy(scene.id);
    try {
      const created = await api.createStudioRun({
        tool: "txt2img",
        model: "flux-schnell",
        prompt,
        params: { aspect: settings?.aspect || "9:16" },
        refs: [],
        title: `Take · ${scene.script.slice(0, 40) || "AI scene"}`,
      });
      let run = await api.getStudioRun(created.run_id);
      const deadline = Date.now() + 180000;
      while (run.status === "queued" || run.status === "processing") {
        if (Date.now() > deadline) throw new Error("Take generation timed out.");
        await new Promise((resolve) => setTimeout(resolve, 2500));
        run = await api.getStudioRun(created.run_id);
      }
      if (run.status !== "completed" || !run.output?.primary) {
        throw new Error(run.error || "Take generation failed.");
      }
      const primary = run.output.primary;
      setScenes((prev) =>
        prev.map((s) =>
          s.id === scene.id
            ? {
                ...s,
                referenceImage: primary.storagePath,
                takes: [
                  ...(s.takes || []),
                  { id: primary.storagePath, path: primary.storagePath, url: primary.url, setting: prompt },
                ],
              }
            : s
        )
      );
      toast("Take ready and selected.", "success");
    } catch (e) {
      toast((e as Error).message || "Could not generate a take.", "error");
    } finally {
      setTakeBusy("");
    }
  }

  function addScene() {
    const id = nextId();
    commitScenes((prev) => [
      ...prev,
      { id, script: "", voice: "", transition: "", media: [], setting: "", shot: "" },
    ]);
    setActiveId(id);
    requestAnimationFrame(() => document.getElementById(`scene-card-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }

  function removeScene(id: string) {
    commitScenes((prev) => {
      if (prev.length <= 1) return prev;
      const next = prev.filter((s) => s.id !== id);
      if (id === activeId) setActiveId(next[0]?.id || "");
      return next;
    });
  }

  function moveScene(id: string, dir: -1 | 1) {
    commitScenes((prev) => {
      const i = prev.findIndex((s) => s.id === id);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const copy = [...prev];
      [copy[i], copy[j]] = [copy[j], copy[i]];
      return copy;
    });
  }

  function splitScene(id: string) {
    commitScenes((prev) => {
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
    const index = scenes.findIndex((s) => s.id === id);
    if (index >= 0) {
      setPreviewTime(
        scenes.slice(0, index).reduce((n, s) => n + scenePlaySeconds(s), 0)
      );
    }
    document.getElementById(`scene-card-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function reorderScene(fromId: string, toId: string) {
    commitScenes((prev) => {
      const from = prev.findIndex((s) => s.id === fromId);
      const to = prev.findIndex((s) => s.id === toId);
      if (from < 0 || to < 0 || from === to) return prev;
      const copy = [...prev];
      const [moved] = copy.splice(from, 1);
      copy.splice(to, 0, moved);
      return copy;
    });
  }

  function addCharacter() {
    setCharacters((prev) => [
      ...prev,
      { id: nextId(), name: "", voice: settings?.voice || "", description: "" },
    ]);
  }

  function updateCharacter(id: string, patch: Partial<CharacterDraft>) {
    setCharacters((prev) => prev.map((c) => (c.id === id ? { ...c, ...patch } : c)));
  }

  async function run(preview: boolean) {
    if (!settings) return;

    if (aiMode) {
      const usable = scenes.filter((s) => s.script.trim() || (s.setting || "").trim());
      if (!usable.length) {
        toast("Add at least one scene with narration and a visual prompt.", "error");
        return;
      }
      const bad = usable.find((s) => !s.script.trim() || !(s.setting || "").trim());
      if (bad) {
        toast("Every AI scene needs both narration and a visual prompt.", "error");
        return;
      }

      const cleanChars = characters
        .filter((c) => c.name.trim())
        .map((c) => ({
          name: c.name.trim(),
          description: c.description || c.name,
          voice: c.voice || settings.voice,
        }));
      const outline = {
        title: title.trim() || "AI film",
        style: handoff?.outline.style || "sports",
        lip_sync: false,
        characters: cleanChars.length
          ? cleanChars
          : [{ name: "Narrator", description: "warm sports narrator", voice: settings.voice }],
        scenes: usable.map((s) => ({
          narration: s.script.trim(),
          dialogue: {},
          setting: (s.setting || "").trim(),
          shot: (s.shot || "").trim(),
          transition: s.transition || undefined,
          reference_image: s.referenceImage || undefined,
        })),
        sources: handoff?.outline.sources || [],
      };

      setIsPreview(preview);
      setSubmitting(true);
      try {
        if (preview) {
          const check = await api.validatePackage(JSON.stringify(outline));
          toast(
            check.valid
              ? `Plan valid · ${check.scenesCount} scenes · ${check.runsNeeded} GPU runs`
              : check.issues.join(" • ") || "Plan needs work.",
            check.valid ? "success" : "error"
          );
          return;
        }
        const res = await api.createFilm({
          idea: "",
          outline: JSON.stringify(outline),
          style: outline.style,
          scenes_count: outline.scenes.length,
          characters: outline.characters,
          captions: settings.captions,
          music: settings.music || "",
          music_volume: settings.music_volume ?? 0.35,
          research: false,
          voice: settings.voice,
          aspect: settings.aspect || "auto",
          workspace_id: handoff?.workspace?.workspace_id || "",
          lip_sync: false,
          timeline,
        });
        toast("Render queued.", "success");
        navigate(`/job/${res.job_id}`);
      } catch (e) {
        toast((e as Error).message || "Failed to start the AI render.", "error");
      } finally {
        setSubmitting(false);
        setIsPreview(false);
      }
      return;
    }

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
            speed: m.speed ?? 1,
            reverse: m.reverse ?? false,
            freeze: m.freeze ?? false,
            motion: m.motion ?? "auto",
            keyframes: m.keyframes ?? [],
          })),
        })),
        characters: cleanChars,
        timeline,
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
            {aiMode
              ? "AI scene draft. Edit narration and visual prompts, then render."
              : "Timeline-first editing. Trim, split, reorder and crop, then render."}
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ minWidth: 210 }}>
            <Segmented
              options={["AI scenes", "Uploaded media"]}
              value={aiMode ? "AI scenes" : "Uploaded media"}
              onChange={(v) => setSceneMode(v === "AI scenes" ? "ai" : "media")}
            />
          </div>
          <div style={{ display: "flex", gap: 4 }}>
            <button
              type="button"
              style={iconBtn}
              title="Undo scene change (Ctrl+Z)"
              disabled={historyState.past === 0}
              onClick={undoScenes}
            >
              ↶
            </button>
            <button
              type="button"
              style={iconBtn}
              title="Redo scene change (Ctrl+Shift+Z)"
              disabled={historyState.future === 0}
              onClick={redoScenes}
            >
              ↷
            </button>
          </div>
          <Button onClick={() => run(true)} loading={isPreview} disabled={submitting}>
            {aiMode ? "Validate plan" : "Preview"}
          </Button>
          <Button variant="primary" onClick={() => run(false)} loading={submitting && !isPreview} disabled={submitting}>
            {aiMode ? "Render AI film" : "Render & export"}
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

            <div
              style={{
                display: "grid",
                gap: 14,
                gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
                alignItems: "start",
              }}
            >
              <div>
                <div className="spread" style={{ marginBottom: 8 }}>
                  <div style={{ fontWeight: 700 }}>Preview</div>
                  <span className="faint" style={{ fontSize: "0.76rem" }}>
                    instant · no GPU
                  </span>
                </div>
                <TimelinePreview
                  scenes={scenes}
                  timeline={timeline}
                  aspect={settings.aspect}
                  total={totalSeconds}
                  time={previewTime}
                  playing={previewPlaying}
                  onTime={setPreviewTime}
                  onPlaying={setPreviewPlaying}
                />
              </div>

              <div style={{ minWidth: 0 }}>
                <div className="spread" style={{ marginBottom: 8, gap: 8, flexWrap: "wrap" }}>
                  <div style={{ fontWeight: 700 }}>Timeline</div>
                  <span className="faint" style={{ fontSize: "0.8rem" }}>
                    {scenes.length} scene{scenes.length === 1 ? "" : "s"} · {totalMedia} clip{totalMedia === 1 ? "" : "s"} ·{" "}
                    {mmss(totalSeconds)}
                  </span>
                  <label className="faint" style={{ display: "flex", gap: 6, alignItems: "center", fontSize: "0.74rem" }}>
                    Zoom
                    <input
                      type="range"
                      min={0.5}
                      max={4}
                      step={0.1}
                      value={timelineZoom}
                      onChange={(e) => setTimelineZoom(Number(e.target.value))}
                      style={{ width: 90 }}
                    />
                  </label>
                </div>
                <Timeline
                  scenes={scenes}
                  activeId={activeId}
                  zoom={timelineZoom}
                  onSelect={selectScene}
                  onReorder={reorderScene}
                />
                <p className="faint" style={{ margin: "8px 0 0", fontSize: "0.74rem" }}>
                  Drag scenes to reorder · click to seek the preview.
                </p>
              </div>
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

        <Card>
          <div className="spread" style={{ marginBottom: 10 }}>
            <h3 className="section-title" style={{ margin: 0 }}>
              Tracks
            </h3>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <Button onClick={addText}>＋ Text</Button>
              <Button onClick={() => setOverlayPicker("image")}>＋ Image overlay</Button>
              <Button onClick={() => setOverlayPicker("video")}>＋ Video overlay</Button>
              <Button onClick={() => setAudioPicker(true)}>＋ Audio</Button>
            </div>
          </div>

          {timeline.texts.length === 0 &&
            timeline.overlays.length === 0 &&
            timeline.audio.length === 0 && (
              <p className="faint" style={{ margin: 0, fontSize: "0.82rem" }}>
                Add titles, a logo/PiP overlay, or extra music/SFX. These are
                burned into the final render.
              </p>
            )}

          {timeline.texts.length > 0 && (
            <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
              <div className="faint" style={{ fontSize: "0.76rem", fontWeight: 700 }}>TEXT</div>
              {timeline.texts.map((text) => (
                <div key={text.id} style={{ display: "grid", gap: 6, padding: 8, border: "1px solid var(--line, rgba(255,255,255,0.12))", borderRadius: 10 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 6 }}>
                    <input className="input" value={text.text} onChange={(e) => patchText(text.id, { text: e.target.value })} />
                    <button type="button" style={iconBtn} title="Remove text" onClick={() => setTimeline((prev) => ({ ...prev, texts: prev.texts.filter((t) => t.id !== text.id) }))}>✕</button>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 6 }}>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>Start<input className="input" type="number" min={0} step={0.1} value={text.start} onChange={(e) => patchText(text.id, { start: Number(e.target.value) })} /></label>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>End<input className="input" type="number" min={0} step={0.1} value={text.end} onChange={(e) => patchText(text.id, { end: Number(e.target.value) })} /></label>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>Size<input className="input" type="number" min={8} max={240} value={text.size} onChange={(e) => patchText(text.id, { size: Number(e.target.value) })} /></label>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>Colour<input className="input" type="color" value={text.color} onChange={(e) => patchText(text.id, { color: e.target.value })} /></label>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8, alignItems: "end" }}>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>X<input type="range" min={0} max={1} step={0.01} value={text.x} onChange={(e) => patchText(text.id, { x: Number(e.target.value) })} style={{ width: "100%" }} /></label>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>Y<input type="range" min={0} max={1} step={0.01} value={text.y} onChange={(e) => patchText(text.id, { y: Number(e.target.value) })} style={{ width: "100%" }} /></label>
                    <label className="faint" style={{ fontSize: "0.72rem", display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" checked={text.background} onChange={(e) => patchText(text.id, { background: e.target.checked })} /> Box</label>
                  </div>
                  <label className="faint" style={{ fontSize: "0.72rem", display: "flex", gap: 6, alignItems: "center" }}>
                    Animation
                    <select className="input" value={text.animation || "none"} onChange={(e) => patchText(text.id, { animation: e.target.value as "none" | "fade" })} style={{ maxWidth: 140 }}>
                      <option value="none">None</option>
                      <option value="fade">Fade in / out</option>
                    </select>
                  </label>
                </div>
              ))}
            </div>
          )}

          {timeline.overlays.length > 0 && (
            <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
              <div className="faint" style={{ fontSize: "0.76rem", fontWeight: 700 }}>OVERLAYS</div>
              {timeline.overlays.map((overlay) => (
                <div key={overlay.id} style={{ display: "grid", gap: 6, padding: 8, border: "1px solid var(--line, rgba(255,255,255,0.12))", borderRadius: 10 }}>
                  <div className="spread" style={{ gap: 8 }}>
                    <span style={{ fontSize: "0.84rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{overlay.name}</span>
                    <button type="button" style={iconBtn} title="Remove overlay" onClick={() => setTimeline((prev) => ({ ...prev, overlays: prev.overlays.filter((o) => o.id !== overlay.id) }))}>✕</button>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0,1fr))", gap: 6 }}>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>Start<input className="input" type="number" min={0} step={0.1} value={overlay.start} onChange={(e) => patchOverlay(overlay.id, { start: Number(e.target.value) })} /></label>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>End<input className="input" type="number" min={0} step={0.1} value={overlay.end} onChange={(e) => patchOverlay(overlay.id, { end: Number(e.target.value) })} /></label>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>Width %<input className="input" type="number" min={3} max={150} value={Math.round(overlay.width * 100)} onChange={(e) => patchOverlay(overlay.id, { width: Number(e.target.value) / 100 })} /></label>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>Opacity<input className="input" type="number" min={0} max={1} step={0.05} value={overlay.opacity} onChange={(e) => patchOverlay(overlay.id, { opacity: Number(e.target.value) })} /></label>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>X<input type="range" min={0} max={1} step={0.01} value={overlay.x} onChange={(e) => patchOverlay(overlay.id, { x: Number(e.target.value) })} style={{ width: "100%" }} /></label>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>Y<input type="range" min={0} max={1} step={0.01} value={overlay.y} onChange={(e) => patchOverlay(overlay.id, { y: Number(e.target.value) })} style={{ width: "100%" }} /></label>
                  </div>
                </div>
              ))}
            </div>
          )}

          {timeline.audio.length > 0 && (
            <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
              <div className="faint" style={{ fontSize: "0.76rem", fontWeight: 700 }}>AUDIO</div>
              {timeline.audio.map((track) => (
                <div key={track.id} style={{ display: "grid", gap: 6, padding: 8, border: "1px solid var(--line, rgba(255,255,255,0.12))", borderRadius: 10 }}>
                  <div className="spread" style={{ gap: 8 }}>
                    <span style={{ fontSize: "0.84rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{track.name}</span>
                    <button type="button" style={iconBtn} title="Remove audio" onClick={() => setTimeline((prev) => ({ ...prev, audio: prev.audio.filter((a) => a.id !== track.id) }))}>✕</button>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(5, minmax(0,1fr))", gap: 6 }}>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>Start<input className="input" type="number" min={0} step={0.1} value={track.start} onChange={(e) => patchAudio(track.id, { start: Number(e.target.value) })} /></label>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>End<input className="input" type="number" min={0} step={0.1} value={track.end} onChange={(e) => patchAudio(track.id, { end: Number(e.target.value) })} /></label>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>Volume<input className="input" type="number" min={0} max={2} step={0.05} value={track.volume} onChange={(e) => patchAudio(track.id, { volume: Number(e.target.value) })} /></label>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>Fade in<input className="input" type="number" min={0} max={10} step={0.1} value={track.fadeIn} onChange={(e) => patchAudio(track.id, { fadeIn: Number(e.target.value) })} /></label>
                    <label className="faint" style={{ fontSize: "0.72rem" }}>Fade out<input className="input" type="number" min={0} max={10} step={0.1} value={track.fadeOut} onChange={(e) => patchAudio(track.id, { fadeOut: Number(e.target.value) })} /></label>
                  </div>
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

                    {aiMode ? (
                      <div style={{ display: "grid", gap: 10, marginTop: 10 }}>
                        <Field label="Visual prompt">
                          <textarea
                            className="textarea"
                            rows={3}
                            placeholder="Describe the shot, people, place and mood…"
                            value={scene.setting || ""}
                            onChange={(e) => updateScene(scene.id, { setting: e.target.value })}
                          />
                        </Field>
                        <Field label="Shot / camera">
                          <input
                            className="input"
                            placeholder="e.g. slow push-in"
                            value={scene.shot || ""}
                            onChange={(e) => updateScene(scene.id, { shot: e.target.value })}
                          />
                        </Field>
                        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                          <Button
                            onClick={() => generateTake(scene)}
                            loading={takeBusy === scene.id}
                            disabled={!!takeBusy}
                          >
                            🎲 Generate take
                          </Button>
                          {scene.referenceImage && (
                            <>
                              <span className="faint" style={{ fontSize: "0.76rem" }}>
                                Using a selected take
                              </span>
                              <Button
                                variant="ghost"
                                onClick={() => updateScene(scene.id, { referenceImage: "" })}
                              >
                                Clear
                              </Button>
                            </>
                          )}
                        </div>
                        {scene.takes && scene.takes.length > 0 && (
                          <div style={{ display: "flex", gap: 8, overflowX: "auto", paddingBottom: 4 }}>
                            {scene.takes.map((take) => (
                              <button
                                key={take.id}
                                type="button"
                                onClick={() => updateScene(scene.id, { referenceImage: take.path })}
                                style={{
                                  flex: "0 0 96px",
                                  padding: 0,
                                  borderRadius: 8,
                                  overflow: "hidden",
                                  cursor: "pointer",
                                  border:
                                    scene.referenceImage === take.path
                                      ? "2px solid var(--accent)"
                                      : "1px solid var(--line, rgba(255,255,255,0.12))",
                                  background: "transparent",
                                }}
                                title={take.setting}
                              >
                                <img src={take.url} alt="Take" style={{ width: "100%", height: 60, objectFit: "cover", display: "block" }} />
                              </button>
                            ))}
                          </div>
                        )}
                        <p className="faint" style={{ margin: 0, fontSize: "0.78rem" }}>
                          Visuals are generated from this prompt. A take is a cheap
                          low-res still you can compare and lock before the full render.
                        </p>
                      </div>
                    ) : (
                      <>
                        <div className="field-label" style={{ marginTop: 6 }}>
                          Media
                        </div>
                        <SceneMediaEditor
                          media={scene.media}
                          onChange={(media) => updateScene(scene.id, { media })}
                        />
                      </>
                    )}
                  </Card>
                </div>
              ))}
            </div>
          )}
        </div>

        <div>
          <Button variant="primary" size="lg" loading={submitting && !isPreview} disabled={submitting} onClick={() => run(false)}>
            {aiMode ? "Render AI film" : "Render & export"}
          </Button>
        </div>

        <AssetPicker
          open={overlayPicker !== null}
          kind={overlayPicker || "image"}
          onClose={() => setOverlayPicker(null)}
          onPick={addOverlay}
          title={overlayPicker === "video" ? "Choose a video overlay" : "Choose an image overlay"}
        />
        <AssetPicker
          open={audioPicker}
          kind="audio"
          onClose={() => setAudioPicker(false)}
          onPick={addAudio}
          title="Choose audio"
        />
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
