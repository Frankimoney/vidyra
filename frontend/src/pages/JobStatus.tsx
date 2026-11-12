import { Link, useNavigate, useParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { useJob } from "../hooks/useJobs";
import { api } from "../services/api";
import { copyText, useToast } from "../components/feedback";
import { FaceRecorder } from "../components/completed/FaceRecorder";
import {
  Button,
  EmptyState,
  ProgressBar,
  Spinner,
} from "../components/ui";

const STAGES = [
  { key: "preparing", label: "Preparing files" },
  { key: "narration", label: "Generating narration" },
  { key: "visuals", label: "Processing media" },
  { key: "captions", label: "Creating captions" },
  { key: "audio", label: "Mixing audio" },
  { key: "finalizing", label: "Finalizing video" },
];

function stageIndex(stage: string): number {
  const idx = STAGES.findIndex((s) => s.key === stage);
  return idx === -1 ? 0 : idx;
}

function DirectorNotes({ filmPlot, settings }: { filmPlot: any; settings?: any }) {
  const director = filmPlot?.director || {};
  const notes: string[] = director?.notes || [];
  const salvaged: string[] = director?.salvaged || [];
  const sources: { title?: string; url?: string }[] = filmPlot?.sources || [];
  const factChecked = director?.factChecked === true && !director?.factCheckError;
  const lipSyncFailed = director?.lipSyncFailed === true;
  const hookText = String(settings?.hook_text || "").trim();
  const hookSubtext = String(settings?.hook_subtext || "").trim();
  const show =
    notes.length > 0 ||
    salvaged.length > 0 ||
    sources.length > 0 ||
    factChecked ||
    lipSyncFailed ||
    hookText;
  if (!show) return null;

  return (
    <div className="card fade-up" style={{ marginTop: 16 }}>
      {lipSyncFailed && (
        <div
          style={{
            marginBottom: 12,
            padding: "10px 12px",
            borderRadius: 10,
            border: "1px solid var(--border)",
            background: "var(--surface-2, rgba(255,255,255,0.04))",
            fontSize: "0.85rem",
          }}
        >
          <strong style={{ display: "block", marginBottom: 2 }}>
            ⚠ Lip-sync unavailable
          </strong>
          <span className="muted">
            The lip-sync step could not finish, so these shots keep their motion but
            the mouths are not animated. The dialogue audio is still there — check the
            RunPod worker logs if this keeps happening.
          </span>
        </div>
      )}

      {hookText && (
        <div style={{ marginBottom: 10 }}>
          <p className="muted" style={{ margin: "0 0 4px", fontSize: "0.8rem" }}>
            Opening hook:
          </p>
          <p style={{ margin: 0, fontSize: "0.9rem", fontWeight: 600 }}>{hookText}</p>
          {hookSubtext && (
            <p className="muted" style={{ margin: "2px 0 0", fontSize: "0.8rem" }}>
              {hookSubtext}
            </p>
          )}
        </div>
      )}

      <div
        className="spread"
        style={{ alignItems: "center", marginBottom: 8 }}
      >
        <h3 style={{ margin: 0 }}>Director's desk</h3>
        {factChecked && (
          <span
            className="muted"
            style={{ fontSize: "0.78rem", color: "var(--success)" }}
          >
            ✓ fact-checked against live sources
          </span>
        )}
        {director?.factCheckError && (
          <span className="muted" style={{ fontSize: "0.78rem", color: "var(--text-3)" }}>
            fact-check skipped: {String(director.factCheckError).slice(0, 80)}
          </span>
        )}
      </div>

      {salvaged.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <p className="muted" style={{ margin: "0 0 4px", fontSize: "0.8rem" }}>
            Re-shots (the director adjusted direction and retried):
          </p>
          {salvaged.map((s, i) => (
            <p key={i} style={{ margin: 0, fontSize: "0.85rem" }}>
              ↻ {s}
            </p>
          ))}
        </div>
      )}

      {notes.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          <p className="muted" style={{ margin: "0 0 4px", fontSize: "0.8rem" }}>
            Notes:
          </p>
          {notes.map((n, i) => (
            <p key={i} style={{ margin: 0, fontSize: "0.85rem" }}>
              • {n}
            </p>
          ))}
        </div>
      )}

      {sources.length > 0 && (
        <div>
          <p className="muted" style={{ margin: "0 0 4px", fontSize: "0.8rem" }}>
            Sources:
          </p>
          {sources.map((s, i) => (
            <p key={i} style={{ margin: 0, fontSize: "0.85rem" }}>
              <a
                href={s.url || "#"}
                target="_blank"
                rel="noreferrer"
                style={{ color: "var(--accent)" }}
              >
                {s.title || s.url}
              </a>
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function ProcessingView({ stage, progress }: { stage: string; progress: number }) {
  const current = stageIndex(stage);
  return (
    <div className="stack-v fade-up" style={{ paddingTop: 30 }}>
      <div className="card" style={{ textAlign: "center" }}>
        <Spinner />
        <h2 style={{ margin: "18px 0 6px" }}>Generating your video…</h2>
        <p className="muted" style={{ margin: 0, fontSize: "0.92rem" }}>
          This usually takes a minute or two.
        </p>
        <div style={{ margin: "22px 0 6px" }}>
          <ProgressBar value={progress} />
        </div>
        <div className="spread" style={{ marginTop: 8 }}>
          <span className="faint" style={{ fontSize: "0.8rem" }}>
            Stage: {STAGES[current]?.label ?? stage}
          </span>
          <span style={{ fontWeight: 700, color: "var(--accent)" }}>{progress}%</span>
        </div>
      </div>

      <div className="card">
        {STAGES.map((s, i) => {
          const done = i < current;
          const active = i === current;
          return (
            <div key={s.key} className="spread" style={{ padding: "9px 0" }}>
              <span
                style={{
                  fontWeight: active ? 700 : 500,
                  color: done ? "var(--success)" : active ? "var(--text)" : "var(--text-3)",
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                }}
              >
                {done ? "✓" : active ? <span className="pulse">●</span> : "○"}
                {s.label}
              </span>
              {done && <span style={{ fontSize: "0.8rem", color: "var(--text-3)" }}>done</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CompletedView({ jobId, title, output }: { jobId: string; title: string; output: any }) {
  const duration = output?.duration ? Math.round(output.duration) : 0;
  const sizeMb = output?.fileSize ? (output.fileSize / (1024 * 1024)).toFixed(1) : "";
  const [src, setSrc] = useState(output?.url as string | undefined);

  // URLs are short-lived signed links (local HMAC or S3 presigned), so refresh
  // the playable URL instead of trusting the one stored on the job document.
  useEffect(() => {
    let alive = true;
    api
      .mediaUrl(jobId)
      .then((r) => {
        if (alive && r.url) setSrc(r.url);
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [jobId]);
  const [name, setName] = useState(title);
  const [editing, setEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(title);
  const [saving, setSaving] = useState(false);
  const [faceJobId, setFaceJobId] = useState<string | null>(null);
  const [showRecorder, setShowRecorder] = useState(false);
  const [showOriginal, setShowOriginal] = useState(false);
  const toast = useToast();

  const { job: faceJob } = useJob(faceJobId ?? "");
  const faceProcessing =
    !!faceJob && (faceJob.status === "queued" || faceJob.status === "processing");
  const faceOutput = faceJob?.status === "completed" ? faceJob.output : null;
  const playerSrc = faceOutput && !showOriginal ? faceOutput.url : src;

  async function saveName() {
    const next = draftTitle.trim();
    if (!next || next === name) {
      setEditing(false);
      return;
    }
    setSaving(true);
    try {
      const res = await api.renameJob(jobId, next);
      setName(res.title);
      setEditing(false);
      toast("Video renamed.", "success");
    } catch (e) {
      toast((e as Error).message || "Rename failed.", "error");
    } finally {
      setSaving(false);
    }
  }

  async function refresh() {
    try {
      const { url } = await api.mediaUrl(jobId);
      setSrc(url);
    } catch {
      /* leave as-is */
    }
  }

  async function share() {
    let link = src;
    if (!link) return;
    try {
      const res = await api.shareUrl(jobId);
      if (res?.pageUrl) link = res.pageUrl;
    } catch {
      /* fall back to current link */
    }
    if (navigator.share && typeof navigator.share === "function") {
      try {
        await navigator.share({ title: name, text: "Check out this video I made with Vidyra", url: link });
        return;
      } catch {
        /* user dismissed — fall through to copy */
      }
    }
    const ok = await copyText(link);
    toast(ok ? "Share link copied to clipboard." : "Could not copy the link.", ok ? "success" : "error");
  }

  return (
    <div className="stack-v fade-up">
      <div className="card" style={{ textAlign: "center", borderColor: "rgba(52,211,153,0.35)" }}>
        <div style={{ fontSize: 34 }}>🎉</div>
        <h2 style={{ margin: "8px 0 2px" }}>Your Video Is Ready</h2>
        {editing ? (
          <div className="stack-h" style={{ justifyContent: "center", gap: 8, marginTop: 4, flexWrap: "wrap" }}>
              <input
                className="input"
                type="text"
                value={draftTitle}
                maxLength={120}
                autoFocus
                onChange={(e) => setDraftTitle(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") saveName();
                  if (e.key === "Escape") setEditing(false);
                }}
                style={{ maxWidth: 320 }}
              />
              <Button variant="primary" loading={saving} onClick={saveName} style={{ width: "auto", padding: "10px 18px" }}>Save</Button>
              <Button variant="ghost" onClick={() => setEditing(false)} style={{ width: "auto", padding: "10px 18px" }}>Cancel</Button>
            </div>
        ) : (
          <p className="muted" style={{ margin: 0, display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}>
            {name}
            <button
              type="button"
              onClick={() => {
                setDraftTitle(name);
                setEditing(true);
              }}
              className="btn-ghost btn"
              style={{ padding: "2px 8px", fontSize: "0.78rem", cursor: "pointer" }}
              title="Rename video"
            >
              ✎
            </button>
          </p>
        )}
      </div>

      <div className="thumb" style={{ width: "78%", margin: "0 auto" }}>
        {faceOutput && !showOriginal && (
          <div
            style={{
              position: "absolute",
              top: 10,
              left: "50%",
              transform: "translateX(-50%)",
              zIndex: 2,
              background: "rgba(16,185,129,0.92)",
              color: "#fff",
              fontSize: "0.75rem",
              fontWeight: 700,
              padding: "4px 10px",
              borderRadius: 999,
              boxShadow: "0 2px 8px rgba(0,0,0,0.3)",
            }}
          >
            With your face recording
          </div>
        )}
        {playerSrc ? (
          <video
            src={playerSrc}
            poster={faceOutput && !showOriginal ? faceOutput.posterUrl || undefined : output?.posterUrl || undefined}
            controls
            playsInline
            style={{ width: "100%", height: "100%" }}
            onError={() => {
              if (playerSrc === src && src === output?.url) refresh();
            }}
          />
        ) : (
          <div className="glass" style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center" }}>
            <Button variant="ghost" onClick={refresh}>Show video</Button>
          </div>
        )}
      </div>

      {faceProcessing && (
        <div className="card">
          <div className="spread">
            <span style={{ fontWeight: 600 }}>Compositing your face recording…</span>
            <span style={{ fontWeight: 700, color: "var(--accent)" }}>{faceJob.progress}%</span>
          </div>
          <div style={{ marginTop: 10 }}>
            <ProgressBar value={faceJob.progress} />
          </div>
        </div>
      )}

      {showRecorder && (
        <FaceRecorder
          jobId={jobId}
          mainSrc={output?.url}
          poster={output?.posterUrl}
          onDone={(newId) => {
            setFaceJobId(newId);
            setShowRecorder(false);
            setShowOriginal(false);
          }}
          onCancel={() => setShowRecorder(false)}
        />
      )}

      {faceOutput && (
        <div className="btn-row" style={{ justifyContent: "center" }}>
          <Button variant="ghost" onClick={() => setShowOriginal((v) => !v)}>
            {showOriginal ? "▶ Show with face" : "↺ Show original video"}
          </Button>
          <Button variant="ghost" onClick={() => setShowRecorder(true)}>
            ↻ Record a different face
          </Button>
        </div>
      )}

      <div className="card">
        <div className="spread">
          <span className="muted">Duration</span>
          <span style={{ fontWeight: 700 }}>{duration ? `${duration}s` : "—"}</span>
        </div>
        <div className="divider" />
        <div className="spread">
          <span className="muted">File size</span>
          <span style={{ fontWeight: 700 }}>{sizeMb ? `${sizeMb} MB` : "—"}</span>
        </div>
      </div>

      <div className="btn-row">
        {src && (
          <a href={src} download={`${name.replace(/\s+/g, "-")}.mp4`} style={{ flex: 1, textDecoration: "none" }}>
            <Button variant="primary">Download MP4</Button>
          </a>
        )}
        <Button variant="ghost" onClick={share}>
          Share
        </Button>
      </div>

      <div className="btn-row">
        <Button
          variant="ghost"
          disabled={!src || faceProcessing || showRecorder}
          onClick={() => setShowRecorder((v) => !v)}
        >
          🙂 {faceOutput ? "Add a different face" : "Add my face"}
        </Button>
      </div>

      <Link to={`/editor/${jobId}`} style={{ textDecoration: "none" }}>
        <Button variant="ghost">✎ Edit this video</Button>
      </Link>

      <Link to="/studio/create" style={{ textDecoration: "none" }}>
        <Button>+ New video</Button>
      </Link>

      <Link to={`/videos?from=${jobId}`} style={{ textDecoration: "none" }}>
        <span className="muted" style={{ display: "block", textAlign: "center" }}>
          Your library →
        </span>
      </Link>
    </div>
  );
}

function FailedView({ job }: { job: any }) {
  const navigate = useNavigate();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const interrupted = /interrupted/i.test(job.error || "");

  async function retry() {
    setBusy(true);
    try {
      const media = (job.media || []).map((m: any) =>
        typeof m === "string" ? m : { path: m.path, duration: m.duration ?? null }
      );
      const res = await api.createJob({
        title: job.title || "My sports video",
        script: job.script || "",
        media,
        settings: job.settings || {},
        cover: job.cover || null,
        // Retry must re-send the storyboard and cast. Without them the backend
        // falls back to its own one-shot planner, which invents a brand-new
        // script from a bare paragraph -- so "same settings" silently meant
        // "make something completely different".
        scenes: job.scenes || [],
        characters: job.characters || [],
      });
      navigate(`/job/${res.job_id}`);
    } catch (e) {
      toast((e as Error).message || "Could not retry.", "error");
      setBusy(false);
    }
  }

  return (
    <div className="stack-v fade-up" style={{ paddingTop: 30 }}>
      <div className="card" style={{ textAlign: "center", borderColor: "rgba(248,113,113,0.35)" }}>
        <div style={{ fontSize: 34 }}>{interrupted ? "🔌" : "⚠️"}</div>
        <h2 style={{ margin: "8px 0 4px" }}>{interrupted ? "This video was interrupted" : "Something went wrong"}</h2>
        <p className="muted" style={{ fontSize: "0.9rem" }}>
          {interrupted
            ? "The app was closed or lost power while this was rendering, so it stopped partway. Your script and uploads are still saved."
            : job.error || job.message || "The video could not be generated."}
        </p>
      </div>
      <Button variant="primary" loading={busy} onClick={retry}>
        ↻ Retry with the same settings
      </Button>
      <Link to="/studio/create" style={{ textDecoration: "none" }}>
        <Button variant="ghost">Start Something New</Button>
      </Link>
    </div>
  );
}

export default function JobStatus() {
  const { jobId = "" } = useParams();
  const { job, loading } = useJob(jobId);

  if (loading) {
    return (
      <div style={{ display: "grid", placeItems: "center", paddingTop: 80 }}>
        <Spinner />
      </div>
    );
  }

  if (!job) {
    return (
      <EmptyState
        icon="🔍"
        title="Video not found"
        hint="It may have been deleted or you don't have access."
      />
    );
  }

  if (job.status === "completed" && job.output?.url) {
    return <CompletedView jobId={job.jobId} title={job.title} output={job.output} />;
  }

  if (job.status === "failed") {
    return <FailedView job={job} />;
  }

  const filmPlot = (job as any).filmPlot;
  return (
    <>
      <ProcessingView stage={job.stage} progress={job.progress} />
      <DirectorNotes filmPlot={filmPlot} settings={(job as any).settings} />
    </>
  );
}