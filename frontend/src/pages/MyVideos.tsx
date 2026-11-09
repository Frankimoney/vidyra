import { useState } from "react";
import { useJobsPage } from "../hooks/useJobs";
import { Button, EmptyState, Spinner } from "../components/ui";
import { VideoCard } from "../components/videos/VideoCard";
import type { JobStatus } from "../types";

type Filter = "All" | "Completed" | "Processing" | "Failed";

const FILTERS: Filter[] = ["All", "Completed", "Processing", "Failed"];
const PAGE_SIZE = 24;

function statusMatches(status: JobStatus, filter: Filter): boolean {
  if (filter === "All") return true;
  if (filter === "Processing") return status === "processing" || status === "queued";
  return status === filter.toLowerCase();
}

export default function MyVideos() {
  const { jobs, loading, loadingMore, done, loadMore } = useJobsPage(PAGE_SIZE);
  const [filter, setFilter] = useState<Filter>("All");

  const filtered = jobs.filter((j) => statusMatches(j.status, filter));
  // A filter can empty the visible page while more videos remain unloaded.
  const canLoadMore = !done;

  return (
    <div className="fade-up">
      <h2 style={{ marginTop: 0 }}>My Videos</h2>

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

          {canLoadMore && (
            <div style={{ display: "grid", placeItems: "center", marginTop: 10 }}>
              <Button onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? <Spinner small /> : `Load ${PAGE_SIZE} more`}
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}