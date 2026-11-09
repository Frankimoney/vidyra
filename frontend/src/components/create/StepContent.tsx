import { useRef, useState } from "react";
import { Button, Field } from "../ui";
import { AssetPicker } from "../AssetPicker";
import { api } from "../../services/api";
import type { LibraryItem, UploadedMedia } from "../../types";

const MAX_BYTES = 50 * 1024 * 1024;
const IMAGE_EXT = ["jpg", "jpeg", "png", "webp"];
const VIDEO_EXT = ["mp4", "mov", "mkv"];

interface Props {
  title: string;
  onTitle: (v: string) => void;
  script: string;
  onScript: (v: string) => void;
  media: UploadedMedia[];
  onMedia: (v: UploadedMedia[]) => void;
  cover: UploadedMedia | null;
  onCover: (v: UploadedMedia | null) => void;
}

function typeOf(name: string): "image" | "video" | null {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (IMAGE_EXT.includes(ext)) return "image";
  if (VIDEO_EXT.includes(ext)) return "video";
  return null;
}

export default function StepContent({ title, onTitle, script, onScript, media, onMedia, cover, onCover }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const coverInputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [coverUploading, setCoverUploading] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState("");

  function cancelUpload() {
    abortRef.current?.abort();
  }

  const words = script.trim() ? script.trim().split(/\s+/).length : 0;

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError("");
    setUploading(true);
    setProgress(0);
    abortRef.current = new AbortController();
    try {
      const incoming = Array.from(files);
      // Extension + MIME check (extension alone is spoofable).
      for (const f of incoming) {
        const kind = typeOf(f.name);
        const mimeOk =
          f.type.startsWith("image/") ||
          ["video/mp4", "video/quicktime", "video/x-matroska", "video/webm"].includes(f.type) ||
          f.type === "";
        if (!kind || !mimeOk) {
          setError(`Unsupported file: ${f.name}`);
          return;
        }
        if (f.size > MAX_BYTES) {
          setError(`${f.name} exceeds 50 MB per file.`);
          return;
        }
      }
      if (incoming.length > 1) {
        const videoFiles = incoming.filter((f) => typeOf(f.name) === "video").length;
        if (videoFiles > 1) {
          setError("Upload one video at a time — it is auto-split into ~4s clips.");
          return;
        }
      }
      if (media.some((m) => m.type === "video") &&
          incoming.some((f) => typeOf(f.name) === "image")) {
        setError("Use images OR one video. You can't mix both.");
        return;
      }
      if (incoming.some((f) => typeOf(f.name) === "video") &&
          media.some((m) => m.type === "image")) {
        setError("Use images OR one video. You can't mix both.");
        return;
      }

      let sum = incoming.reduce((a, f) => a + f.size, 0);
      if (sum > MAX_BYTES) {
        setError(`Total media exceeds 50 MB.`);
        return;
      }

      const added: UploadedMedia[] = [];
      let done = 0;
      for (const file of incoming) {
        const kind = typeOf(file.name);
        if (!kind) {
          setError(`Unsupported file: ${file.name}`);
          continue;
        }
        const result = await api.uploadFileWithProgress(
          file,
          (pct) => setProgress(Math.round(((done + pct / 100) / incoming.length) * 100)),
          abortRef.current?.signal
        );
        done += 1;
        setProgress(Math.round((done / incoming.length) * 100));
        for (const item of result.items) {
          let url = kind === "image" ? URL.createObjectURL(file) : "";
          if (kind === "video") {
            try {
              const res = await api.preview(item.storagePath);
              url = res.url;
            } catch {
              /* ignore — empty thumb */
            }
          }
          added.push({
            id: item.storagePath,
            name: item.name,
            type: item.type,
            url,
            size: item.size,
            storagePath: item.storagePath,
            duration: item.type === "video" ? item.duration ?? 4 : undefined,
          });
        }
      }
      onMedia([...media, ...added]);
    } catch (e) {
      setError((e as Error).message || "Upload failed.");
    } finally {
      setUploading(false);
      setProgress(0);
      abortRef.current = null;
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function move(index: number, dir: -1 | 1) {
    const next = [...media];
    const target = index + dir;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    onMedia(next);
  }

  function remove(index: number) {
    const target = media[index];
    if (target?.url?.startsWith("blob:")) {
      try {
        URL.revokeObjectURL(target.url);
      } catch {
        /* ignore */
      }
    }
    onMedia(media.filter((_, i) => i !== index));
  }

  function addFromLibrary(item: LibraryItem) {
    setError("");
    const type: "image" | "video" = item.kind === "video" ? "video" : "image";
    if (type === "video" && media.some((m) => m.type === "image")) {
      setError("Use images OR one video. You can't mix both.");
      return;
    }
    if (type === "image" && media.some((m) => m.type === "video")) {
      setError("Use images OR one video. You can't mix both.");
      return;
    }
    onMedia([
      ...media,
      {
        id: item.path,
        name: item.name,
        type,
        url: item.url,
        size: item.size,
        storagePath: item.path,
      },
    ]);
  }

  async function handleCover(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError("");
    setCoverUploading(true);
    try {
      const file = files[0];
      if (typeOf(file.name) !== "image") {
        setError("The cover must be an image (jpg, png or webp).");
        return;
      }
      const result = (await api.uploadFile(file)).items[0];
      onCover({
        id: result.storagePath,
        name: result.name,
        type: "image",
        url: URL.createObjectURL(file),
        size: result.size,
        storagePath: result.storagePath,
      });
    } catch (e) {
      setError((e as Error).message || "Cover upload failed.");
    } finally {
      setCoverUploading(false);
      if (coverInputRef.current) coverInputRef.current.value = "";
    }
  }

  return (
    <div className="stack-v fade-up">
      <Field label="Video name (optional)">
        <input
          className="input"
          type="text"
          placeholder="My sports video"
          value={title}
          maxLength={120}
          onChange={(e) => onTitle(e.target.value)}
        />
      </Field>

      <Field label="Script">
        <textarea
          className="textarea"
          placeholder="Paste or write your sports story…"
          value={script}
          onChange={(e) => onScript(e.target.value)}
          maxLength={6000}
        />
        <div className="spread faint" style={{ marginTop: 6, fontSize: "0.8rem" }}>
          <span>{words} words</span>
          <span>{script.length}/6000</span>
        </div>
      </Field>

      <Field label="Media — images, or one video (auto-split into ~4s clips)">
        <input
          ref={inputRef}
          type="file"
          accept="image/*,video/mp4,video/quicktime,video/x-matroska"
          multiple
          style={{ display: "none" }}
          onChange={(e) => handleFiles(e.target.files)}
        />
        <Button variant="ghost" onClick={() => inputRef.current?.click()} loading={uploading}>
          📤 Upload media{uploading && progress > 0 ? ` ${progress}%` : ""}
        </Button>
        {uploading && (
          <Button variant="ghost" onClick={cancelUpload} style={{ marginLeft: 8 }}>
            ✕ Cancel
          </Button>
        )}
        <Button variant="ghost" onClick={() => setPickerOpen(true)} style={{ marginLeft: 8 }}>
          🗂 From library / AI Studio
        </Button>
        <AssetPicker
          open={pickerOpen}
          onClose={() => setPickerOpen(false)}
          onPick={addFromLibrary}
          title="Add media from library"
        />
        {error && (
          <div style={{ color: "var(--danger)", fontSize: "0.84rem", marginTop: 8 }}>{error}</div>
        )}
      </Field>

      <Field label="Cover image (used as the hook background + thumbnail — optional)">
        <input
          ref={coverInputRef}
          type="file"
          accept="image/*"
          style={{ display: "none" }}
          onChange={(e) => handleCover(e.target.files)}
        />
        {cover ? (
          <div className="spread" style={{ gap: 10, flexWrap: "wrap" }}>
            <div className="stack-h" style={{ gap: 10, minWidth: 0, flex: 1 }}>
              <div className="thumb" style={{ width: 56, height: 80, flexShrink: 0 }}>
                <img src={cover.url} alt={cover.name} />
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: "0.84rem", fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {cover.name}
                </div>
                <p className="faint" style={{ margin: "2px 0 0", fontSize: "0.76rem" }}>
                  Shown behind the opening hook text.
                </p>
              </div>
            </div>
            <Button variant="ghost" onClick={() => onCover(null)}>Remove</Button>
          </div>
        ) : (
          <div className="stack-h" style={{ gap: 10, alignItems: "center" }}>
            <Button variant="ghost" onClick={() => coverInputRef.current?.click()} loading={coverUploading}>
              🖼 Add cover image
            </Button>
            <span className="faint" style={{ fontSize: "0.78rem" }}>
              Falls back to your first clip if not set.
            </span>
          </div>
        )}
      </Field>

      {media.length > 0 && (
        <div className="card">
          <div className="media-grid">
            {media.map((m, i) => (
              <div key={`${m.name}-${i}`} className="thumb">
                {m.type === "image" ? (
                  <img src={m.url} alt={m.name} />
                ) : (
                  <video src={m.url} muted playsInline preload="metadata" />
                )}
                <div
                  style={{
                    position: "absolute",
                    inset: 0,
                    display: "flex",
                    justifyContent: "space-between",
                    padding: 6,
                  }}
                >
                  <div className="glass" style={{ padding: "2px 8px", fontSize: "0.72rem", fontWeight: 700 }}>
                    {i + 1}
                  </div>
                  <button
                    type="button"
                    onClick={() => remove(i)}
                    className="glass"
                    style={{
                      border: "none",
                      color: "#fff",
                      width: 26,
                      height: 26,
                      borderRadius: "50%",
                      cursor: "pointer",
                      background: "rgba(239,68,68,0.8)",
                    }}
                  >
                    ✕
                  </button>
                </div>
              </div>
            ))}
          </div>
          {media.length > 1 && (
            <div className="stack-v" style={{ marginTop: 12 }}>
              {media.map((m, i) => (
                <div key={m.name} className="spread" style={{ gap: 10 }}>
                  <span
                    style={{
                      fontWeight: 600,
                      fontSize: "0.88rem",
                      minWidth: 0,
                      flex: 1,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {i + 1}. {m.name}
                  </span>
                  <div className="stack-h">
                    <button type="button" className="btn-ghost btn" style={{ width: 34, padding: 6 }} disabled={i === 0} onClick={() => move(i, -1)}>▲</button>
                    <button type="button" className="btn-ghost btn" style={{ width: 34, padding: 6 }} disabled={i === media.length - 1} onClick={() => move(i, 1)}>▼</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}