import { useEffect, useState } from "react";
import { api } from "../services/api";
import { useConfirm, useToast } from "./feedback";
import { EmptyState, Spinner } from "./ui";
import type { LibraryItem } from "../types";

function kindIcon(kind: string): string {
  if (kind === "video") return "🎬";
  if (kind === "audio") return "🎵";
  return "🖼";
}

function prettySize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Modal browser for everything the user owns: uploads, AI Studio results and
 * rendered videos. Used to reuse an asset instead of re-uploading it.
 */
export function AssetPicker({
  open,
  onClose,
  onPick,
  kind,
  title = "Choose from library",
}: {
  open: boolean;
  onClose: () => void;
  onPick: (item: LibraryItem) => void;
  kind?: "image" | "video" | "audio";
  title?: string;
}) {
  const toast = useToast();
  const confirm = useConfirm();
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "image" | "video">(
    kind === "image" || kind === "video" ? kind : "all"
  );

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    api
      .library(kind)
      .then((res) => setItems(res.items))
      .catch((e) => toast(e.message || "Could not load library.", "error"))
      .finally(() => setLoading(false));
  }, [open, kind]);

  async function removeItem(it: LibraryItem) {
    const ok = await confirm({
      title: "Delete this file?",
      message: `“${it.name}” will be removed from your library. A rendered video, once deleted, can't be recovered.`,
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    setDeleting(it.path);
    try {
      await api.deleteLibraryItem(it.path);
      setItems((prev) => prev.filter((x) => x.path !== it.path));
      toast("Deleted.", "success");
    } catch (e: any) {
      toast(e?.message || "Could not delete that file.", "error");
    } finally {
      setDeleting(null);
    }
  }

  if (!open) return null;

  const shown = items.filter(
    (it) => filter === "all" || it.kind === filter
  );

  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%",
          maxWidth: 760,
          maxHeight: "85vh",
          overflow: "auto",
          background: "var(--surface, #17171c)",
          border: "1px solid var(--line, rgba(255,255,255,0.12))",
          borderRadius: 16,
          padding: 18,
        }}
      >
        <div className="spread" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>{title}</h3>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "var(--text-3)",
              fontSize: "1.1rem",
              cursor: "pointer",
            }}
          >
            ✕
          </button>
        </div>

        {!kind && (
          <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
            {(["all", "image", "video"] as const).map((f) => (
              <button
                key={f}
                type="button"
                className={`seg-btn ${filter === f ? "active" : ""}`}
                onClick={() => setFilter(f)}
              >
                {f === "all" ? "All" : f === "image" ? "Images" : "Videos"}
              </button>
            ))}
          </div>
        )}

        {loading ? (
          <div style={{ display: "grid", placeItems: "center", padding: 40 }}>
            <Spinner />
          </div>
        ) : shown.length === 0 ? (
          <EmptyState
            icon="🗂"
            title="Nothing here yet"
            hint="Upload media or generate something in AI Studio, then it shows up here."
          />
        ) : (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(130px, 1fr))",
              gap: 10,
            }}
          >
            {shown.map((it) => (
              <div
                key={it.path}
                style={{ position: "relative", minWidth: 0 }}
              >
                <button
                  type="button"
                  onClick={() => {
                    onPick(it);
                    onClose();
                  }}
                  title={it.name}
                  style={{
                    padding: 0,
                    width: "100%",
                    border: "1px solid var(--line, rgba(255,255,255,0.12))",
                    borderRadius: 10,
                    overflow: "hidden",
                    background: "rgba(255,255,255,0.03)",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  <div
                    style={{
                      height: 96,
                      background: "#000",
                      display: "grid",
                      placeItems: "center",
                      overflow: "hidden",
                    }}
                  >
                    {it.kind === "image" ? (
                      <img
                        src={it.url}
                        alt={it.name}
                        style={{ width: "100%", height: "100%", objectFit: "cover" }}
                      />
                    ) : it.kind === "video" ? (
                      <video
                        src={it.url}
                        muted
                        playsInline
                        preload="metadata"
                        style={{ width: "100%", height: "100%", objectFit: "cover" }}
                      />
                    ) : (
                      <span style={{ fontSize: 28 }}>{kindIcon(it.kind)}</span>
                    )}
                  </div>
                  <div style={{ padding: "6px 8px" }}>
                    <div
                      style={{
                        fontSize: "0.78rem",
                        fontWeight: 600,
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                      }}
                    >
                      {it.name}
                    </div>
                    <div className="faint" style={{ fontSize: "0.7rem" }}>
                      {it.source === "studio" ? "AI Studio" : it.source} ·{" "}
                      {prettySize(it.size)}
                    </div>
                  </div>
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    removeItem(it);
                  }}
                  disabled={deleting === it.path}
                  title="Delete from library"
                  aria-label={`Delete ${it.name}`}
                  style={{
                    position: "absolute",
                    top: 6,
                    right: 6,
                    width: 24,
                    height: 24,
                    display: "grid",
                    placeItems: "center",
                    border: "none",
                    borderRadius: 999,
                    background: "rgba(0,0,0,0.55)",
                    color: "#fff",
                    fontSize: "0.8rem",
                    cursor: "pointer",
                    opacity: 0.92,
                  }}
                >
                  {deleting === it.path ? "…" : "🗑"}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
