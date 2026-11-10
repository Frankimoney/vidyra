import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import { useJobs } from "../hooks/useJobs";
import { Button, EmptyState } from "../components/ui";
import { VideoCard } from "../components/videos/VideoCard";
import { api } from "../services/api";
import type { PlanningProject } from "../services/api";

const EXAMPLES = [
  "A shy robot learns to make friends at a bus stop",
  "A 30-second explainer on why sourdough needs a starter",
  "My cat reviews a laser pointer like a serious critic",
  "A mystery: the lighthouse that goes dark every winter",
];

export default function Home() {
  const { user } = useAuth();
  const { jobs } = useJobs();
  const navigate = useNavigate();
  const [idea, setIdea] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const [projects, setProjects] = useState<PlanningProject[]>([]);
  const [castCount, setCastCount] = useState(0);

  useEffect(() => {
    api
      .listProjects()
      .then((r) => setProjects((r.items || []).filter((p) => p.status !== "filmed")))
      .catch(() => setProjects([]));
  }, []);

  useEffect(() => {
    // Count only — the Cast page is what renders faces.
    api
      .listCharacters()
      .then((r) => setCastCount(r.length))
      .catch(() => setCastCount(0));
  }, []);

  /**
   * Start a planning session. If the planning agent isn't available we must
   * never dead-end someone who just typed an idea — fall through to the
   * one-shot film flow with the same text so they always get something made.
   */
  async function startIdea(text: string) {
    const clean = text.trim();
    if (clean.length < 3) {
      setStartError("Describe your video in a sentence or two.");
      return;
    }
    setStarting(true);
    setStartError("");
    try {
      const r = await api.createProject(clean, "auto");
      navigate(`/film?mode=director&project=${encodeURIComponent(r.project.projectId)}`);
    } catch {
      navigate(`/film?mode=film&idea=${encodeURIComponent(clean)}`);
    } finally {
      setStarting(false);
    }
  }

  const inProgress = jobs.filter(
    (j) => j.status === "queued" || j.status === "processing"
  );

  return (
    <div className="fade-up">
      <header className="spread" style={{ marginBottom: 18 }}>
        <div className="logo">
          vi<span className="bolt">dyra</span>
        </div>
        <Link
          to="/profile"
          title="Account"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            maxWidth: "55%",
            color: "var(--text-2)",
            textDecoration: "none",
            fontSize: "0.86rem",
          }}
        >
          <span className="avatar" style={{ width: 28, height: 28, fontSize: 12 }}>
            {(user?.email?.[0] || "V").toUpperCase()}
          </span>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {user?.displayName || user?.email}
          </span>
        </Link>
      </header>

      <section className="hero">
        <h2>What should we make?</h2>
        <p className="tagline">
          One sentence is enough. We&apos;ll ask what we need to know, show you the
          plan, and only start spending once you say go.
        </p>

        <div style={{ maxWidth: 620, margin: "18px auto 0", textAlign: "left" }}>
          <textarea
            className="input"
            rows={3}
            placeholder="A shy robot learns to make friends at a bus stop"
            value={idea}
            maxLength={2000}
            onChange={(e) => {
              setIdea(e.target.value);
              if (startError) setStartError("");
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) startIdea(idea);
            }}
            style={{ resize: "vertical", minHeight: 84 }}
          />
          <div
            className="spread"
            style={{ marginTop: 10, alignItems: "center", gap: 10 }}
          >
            <span className="faint" style={{ fontSize: "0.76rem" }}>
              Tip: ⌘/Ctrl + Enter to start
            </span>
            <Button
              variant="primary"
              onClick={() => startIdea(idea)}
              disabled={starting || !idea.trim()}
            >
              {starting ? "Setting up…" : "Plan my video"}
            </Button>
          </div>
          {startError && (
            <p
              className="faint"
              style={{ color: "var(--danger)", fontSize: "0.8rem", marginTop: 8 }}
            >
              {startError}
            </p>
          )}
        </div>

        <div className="spread" style={{ maxWidth: 620, margin: "16px auto 0", gap: 6 }}>
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              className="input"
              onClick={() => startIdea(ex)}
              disabled={starting}
              style={{
                cursor: "pointer",
                textAlign: "left",
                fontSize: "0.8rem",
                padding: "8px 10px",
                fontWeight: 500,
              }}
            >
              {ex}
            </button>
          ))}
        </div>
      </section>

      {/* The Cast sits just under the front door: characters are how a
          beginner gets continuity without touching a prompt twice. */}
      <section style={{ maxWidth: 620, margin: "18px auto 0" }}>
        <div
          className="spread"
          style={{
            gap: 12,
            padding: "12px 14px",
            borderRadius: 14,
            border: "1px solid var(--border)",
            background: "var(--surface)",
          }}
        >
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 700, fontSize: "0.9rem" }}>
              Got a character?
            </div>
            <div className="faint" style={{ fontSize: "0.8rem" }}>
              {castCount > 0
                ? `${castCount} in your cast — ready to reuse in any video.`
                : "Add a photo once and they look the same in every video."}
            </div>
          </div>
          <Button onClick={() => navigate("/characters")}>
            {castCount > 0 ? "Open cast" : "Add one"}
          </Button>
        </div>
      </section>

      {projects.length > 0 && (
        <section style={{ marginTop: 18 }}>
          <h3 className="section-title" style={{ margin: "0 0 10px" }}>
            Pick up where you left off
          </h3>
          <div style={{ display: "grid", gap: 8 }}>
            {projects.slice(0, 3).map((p) => (
              <Link
                key={p.projectId}
                to={`/film?mode=director&project=${encodeURIComponent(p.projectId)}`}
                className="glass quick-tile"
                style={{ textDecoration: "none" }}
              >
                <span
                  style={{
                    fontWeight: 700,
                    fontSize: "0.9rem",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {p.title || "Untitled project"}
                </span>
                <span className="faint" style={{ fontSize: "0.74rem" }}>
                  {p.status === "planned"
                    ? `Plan ready · ${p.scenesCount || 0} scenes`
                    : "Still planning"}
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}

      {inProgress.length > 0 && (
        <div style={{ marginTop: 22 }}>
          <div className="spread">
            <h3 className="section-title" style={{ margin: 0 }}>In the oven</h3>
            <Link to="/videos" style={{ color: "var(--accent)", fontSize: "0.86rem" }}>View all</Link>
          </div>
          <div className="h-rail" style={{ marginTop: 10 }}>
            {inProgress.map((j) => (
              <div key={j.jobId} style={{ flex: "0 0 150px" }}>
                <VideoCard job={j} showDelete={false} />
              </div>
            ))}
          </div>
        </div>
      )}


      <div style={{ marginTop: 26 }}>
        <div className="spread" style={{ marginBottom: 12 }}>
          <h3 className="section-title" style={{ margin: 0 }}>
            Recent videos
          </h3>
          <Link to="/videos" style={{ color: "var(--accent)", fontSize: "0.86rem" }}>
            View library
          </Link>
        </div>

        {jobs.length === 0 ? (
          <EmptyState
            icon="🎬"
            title="No videos yet"
            hint="Start with a sentence above, or open New video for recipes."
          />
        ) : (
          <div className="video-grid">
            {jobs.slice(0, 6).map((job) => (
              <VideoCard key={job.jobId} job={job} />
            ))}
          </div>
        )}
      </div>

    </div>
  );
}