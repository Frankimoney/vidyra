import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../services/api";
import { useConfirm, useToast } from "../feedback";
import { Badge, ProgressBar } from "../ui";
import type { JobItem } from "../../types";

function timeAgo(iso: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function DeleteButton({ job }: { job: JobItem }) {
  const [busy, setBusy] = useState(false);
  const confirm = useConfirm();
  const toast = useToast();

  async function remove() {
    const ok = await confirm({
      title: "Delete this video?",
      message: "The finished video, cover, and its media clips will be removed. This can't be undone.",
      confirmLabel: "Delete",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.deleteJob(job.jobId);
      toast("Video deleted.", "success");
    } catch (e) {
      toast((e as Error).message || "Delete failed.", "error");
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      style={{
        position: "absolute",
        top: 8,
        right: 8,
        zIndex: 3,
        width: 34,
        height: 34,
        display: "grid",
        placeItems: "center",
        padding: 0,
        borderRadius: 10,
        border: "1px solid rgba(255,255,255,0.22)",
        background: "rgba(10,10,14,0.72)",
        color: "#fff",
        fontSize: "0.95rem",
        cursor: "pointer",
        lineHeight: 1,
        boxShadow: "0 2px 10px rgba(0,0,0,0.35)",
        backdropFilter: "blur(2px)",
      }}
      title="Delete"
      disabled={busy || job.status === "processing"}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        remove();
      }}
    >
      {busy ? "…" : "🗑"}
    </button>
  );
}

/** Vertical 9:16 video card: poster, status, progress, duration, delete. */
export function VideoCard({
  job,
  showDelete = true,
}: {
  job: JobItem;
  showDelete?: boolean;
}) {
  const [src, setSrc] = useState(job.output?.url);
  // Prefer the live document's URL over the value captured at mount. Reading
  // only `src` pinned cards that mounted while the job was still queued: the
  // realtime update delivered `status: "completed"` with a URL, but `src` was
  // still undefined, so the placeholder never cleared until the user navigated
  // away and back.
  const done = job.status === "completed" && !!(job.output?.url || src);
  const playable = job.output?.url || src;

  async function refresh() {
    try {
      const { url } = await api.mediaUrl(job.jobId);
      setSrc(url);
    } catch {
      /* leave as-is */
    }
  }

  return (
    <Link to={`/job/${job.jobId}`} style={{ textDecoration: "none", display: "block" }}>
      <div>
        <div className="thumb">
          {showDelete && <DeleteButton job={job} />}
          {done ? (
            job.output?.posterUrl ? (
              <img
                src={job.output.posterUrl}
                alt=""
                style={{ width: "100%", height: "100%", objectFit: "cover" }}
              />
            ) : (
              <video
                src={playable}
                muted
                playsInline
                preload="metadata"
                onError={() => {
                  // Only ask for a fresh signed URL when the one we are showing
                  // expired -- otherwise a 404 would loop a request per card.
                  if (playable === job.output?.url) refresh();
                }}
              />
            )
          ) : (
            <div
              style={{
                height: "100%",
                display: "grid",
                placeItems: "center",
                color: "var(--text-3)",
                fontSize: 30,
              }}
            >
              {job.status === "failed" ? "⚠️" : "🎬"}
            </div>
          )}
          {done && job.output?.duration ? (
            <span
              className="dur-chip"
              title={`${job.output.duration.toFixed(1)}s`}
            >
              {Math.round(job.output.duration)}s
            </span>
          ) : null}
          {job.status === "processing" && (
            <div className="glass" style={{ position: "absolute", inset: "auto 8px 8px", padding: "8px 10px" }}>
              <div className="spread" style={{ marginBottom: 6 }}>
                <span style={{ fontSize: "0.72rem", fontWeight: 700, textTransform: "capitalize" }}>
                  {job.stage}
                </span>
                <span style={{ fontSize: "0.72rem", color: "var(--accent)" }}>{job.progress}%</span>
              </div>
              <ProgressBar value={job.progress} />
            </div>
          )}
        </div>
        <div style={{ marginTop: 8 }}>
          <div
            style={{
              fontWeight: 700,
              fontSize: "0.92rem",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {job.title}
          </div>
          <div className="spread" style={{ marginTop: 3 }}>
            <span className="faint" style={{ fontSize: "0.76rem" }}>
              {timeAgo(job.createdAt)}
            </span>
            <Badge status={job.status} />
          </div>
        </div>
      </div>
    </Link>
  );
}