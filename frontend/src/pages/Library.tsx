import { useEffect, useState } from "react";
import { api } from "../services/api";
import { useJobsPage } from "../hooks/useJobs";
import { Badge, Button, Card, EmptyState, Spinner } from "../components/ui";
import { VideoCard } from "../components/videos/VideoCard";
import { RunResult } from "./Studio";
import type { JobStatus, StudioRun } from "../types";

type Tab = "videos" | "studio";
type Filter = "All" | "Completed" | "Processing" | "Failed";

const FILTERS: Filter[] = ["All", "Completed", "Processing", "Failed"];
const PAGE_SIZE = 24;

function statusMatches(status: JobStatus, filter: Filter): boolean {
  if (filter === "All") return true;
  if (filter === "Processing") return status === "processing" || status === "queued";
  return status === filter.toLowerCase();
}

function when(iso: string): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export default function Library() {
  const [tab, setTab] = useState<Tab>("videos");
  const [filter, setFilter] = useState<Filter>("All");
  const [runs, setRuns] = useState<StudioRun[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const { jobs, loading, loadingMore, done, loadMore } = useJobsPage(PAGE_SIZE);

  useEffect(() => {
    if (tab !== "studio") return;
    let cancelled = false;
    setRunsLoading(true);
    api
      .listStudioRuns()
      .then((items) => {
        if (!cancelled) setRuns(items || []);
      })
      .catch(() => {
        if (!cancelled) setRuns([]);
      })
      .finally(() => {
        if (!cancelled) setRunsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [tab]);

  const filtered = jobs.filter((j) => statusMatches(j.status, filter));

  return (
    <div className="fade-up">
      <header style={{ marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>Library</h2>
        <p className="faint" style={{ fontSize: "0.86rem", margin: "4px 0 0" }}>
          Finished videos, images, and recent tool runs.
        </p>
      </header>

      <div className="segmented" style={{ marginBottom: 16 }}>
        <button
          type="button"
          className={`seg-btn ${tab === "videos" ? "active" : ""}`}
          onClick={() => setTab("videos")}
        >
          Videos
        </button>
        <button
          type="button"
          className={`seg-btn ${tab === "studio" ? "active" : ""}`}
          onClick={() => setTab("studio")}
        >
          Studio runs
        </button>
      </div>

      {tab === "videos" ? (
        <>
          <div className="segmented" style={{ marginBottom: 16 }}>
            {FILTERS.map((f) => (
              <button
                key={f}
                type="button"
                className={`seg-btn ${filter === f ? "active" : ""}`}
                onClick={() => setFilter(f)}
              >
                {f}
              </button>
            ))}
          </div>

          {loading ? (
            <EmptyState icon="⏳" title="Loading…" />
          ) : filtered.length === 0 ? (
            <EmptyState
              icon="🎞️"
              title="Nothing here"
              hint="Videos you create will show up in your library."
            />
          ) : (
            <>
              <div className="video-grid">
                {filtered.map((job) => (
                  <VideoCard key={job.jobId} job={job} />
                ))}
              </div>
              <p className="faint" style={{ textAlign: "center", fontSize: "0.8rem", margin: "18px 0 0" }}>
                Showing {filtered.length}
                {done ? "" : ` of ${jobs.length}+`}
              </p>
              {!done && (
                <div style={{ display: "grid", placeItems: "center", marginTop: 10 }}>
                  <Button onClick={loadMore} disabled={loadingMore}>
                    {loadingMore ? <Spinner small /> : `Load ${PAGE_SIZE} more`}
                  </Button>
                </div>
              )}
            </>
          )}
        </>
      ) : runsLoading ? (
        <EmptyState icon="⏳" title="Loading runs…" />
      ) : runs.length === 0 ? (
        <EmptyState
          icon="🎬"
          title="No Studio runs yet"
          hint="Images and videos from the Tools tab will appear here."
        />
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          {runs.map((run) => (
            <Card key={run.run_id}>
              <div className="spread" style={{ gap: 10 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontWeight: 700 }}>{run.title || run.prompt || "Untitled"}</div>
                  <div className="faint" style={{ fontSize: "0.8rem", marginTop: 2 }}>
                    {run.tool} · {run.model} · {when(run.created_at)}
                  </div>
                </div>
                <Badge status={run.status} />
              </div>
              {run.status === "completed" && run.output?.primary && (
                <div style={{ marginTop: 10 }}>
                  <RunResult run={run} />
                  <a
                    href={run.output.primary.url}
                    target="_blank"
                    rel="noreferrer"
                    style={{ display: "inline-block", marginTop: 8, color: "var(--accent)", fontSize: "0.85rem" }}
                  >
                    Open full size ↗
                  </a>
                </div>
              )}
              {run.status === "failed" && run.error && (
                <p style={{ margin: "10px 0 0", color: "var(--danger)", fontSize: "0.84rem" }}>
                  {run.error}
                </p>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
