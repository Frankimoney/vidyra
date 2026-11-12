import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../services/api";
import type { PlanningProject } from "../services/api";
import { useJobs } from "../hooks/useJobs";
import { Badge, Button, Card, EmptyState, Spinner } from "../components/ui";
import type { StudioRun } from "../types";

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

function projectLabel(project: PlanningProject): string {
  if (project.status === "filmed") return "Filmed";
  if (project.status === "planned") return "Plan ready";
  return "Planning";
}

export default function StudioScenes() {
  const navigate = useNavigate();
  const { jobs } = useJobs();
  const [projects, setProjects] = useState<PlanningProject[]>([]);
  const [runs, setRuns] = useState<StudioRun[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.allSettled([api.listProjects(), api.listStudioRuns()])
      .then(([projectResult, runResult]) => {
        if (cancelled) return;
        if (projectResult.status === "fulfilled") {
          setProjects(projectResult.value.items || []);
        }
        if (runResult.status === "fulfilled") {
          setRuns(runResult.value || []);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const recentJobs = useMemo(
    () =>
      jobs
        .filter((job) => job.status === "completed" || job.status === "processing" || job.status === "queued")
        .slice(0, 10),
    [jobs]
  );

  const completedRuns = useMemo(
    () => runs.filter((run) => run.status === "completed" && run.output?.primary).slice(0, 10),
    [runs]
  );

  function openRun(run: StudioRun) {
    const primary = run.output?.primary;
    if (!primary) return;
    navigate("/studio/scenes/editor", {
      state: {
        preload: {
          path: primary.storagePath,
          name: primary.name,
          kind: primary.kind,
          size: primary.size,
          url: primary.url,
          duration: primary.duration,
        },
      },
    });
  }

  if (loading) {
    return (
      <div style={{ display: "grid", placeItems: "center", minHeight: "30dvh" }}>
        <Spinner />
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <Card>
        <div className="spread" style={{ gap: 12, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontWeight: 700 }}>Scenes</div>
            <p className="faint" style={{ fontSize: "0.84rem", margin: "3px 0 0" }}>
              Open a plan, edit a rendered film, or send a Studio output into the timeline.
            </p>
          </div>
          <Link to="/studio/scenes/editor" style={{ textDecoration: "none" }}>
            <Button variant="primary">New timeline</Button>
          </Link>
        </div>
      </Card>

      <section>
        <h3 className="section-title" style={{ margin: "0 0 8px" }}>
          Projects
        </h3>
        {projects.length === 0 ? (
          <EmptyState
            icon="🎬"
            title="No projects yet"
            hint="Start with Create — the Director will give you a plan before anything renders."
          />
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {projects.map((project) => (
              <Card key={project.projectId}>
                <div className="spread" style={{ gap: 10, flexWrap: "wrap" }}>
                  <div style={{ minWidth: 200 }}>
                    <div style={{ fontWeight: 700 }}>{project.title || "Untitled project"}</div>
                    <div className="faint" style={{ fontSize: "0.8rem" }}>
                      {projectLabel(project)} · {project.scenesCount || 0} scenes ·{" "}
                      {project.charactersCount || 0} characters · {when(project.updatedAt)}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    <Link to={`/studio/create?project=${encodeURIComponent(project.projectId)}`} style={{ textDecoration: "none" }}>
                      <Button>Open plan</Button>
                    </Link>
                    {project.jobId && (
                      <Link to={`/studio/scenes/${project.jobId}`} style={{ textDecoration: "none" }}>
                        <Button variant="primary">Open scenes</Button>
                      </Link>
                    )}
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
      </section>

      <section>
        <h3 className="section-title" style={{ margin: "0 0 8px" }}>
          Films
        </h3>
        {recentJobs.length === 0 ? (
          <EmptyState icon="🎞️" title="No films yet" hint="Rendered films will appear here for timeline editing." />
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {recentJobs.map((job) => (
              <Card key={job.jobId}>
                <div className="spread" style={{ gap: 10, flexWrap: "wrap" }}>
                  <div style={{ minWidth: 200 }}>
                    <div style={{ fontWeight: 700 }}>{job.title || "Untitled film"}</div>
                    <div className="faint" style={{ fontSize: "0.8rem" }}>
                      {when(job.createdAt)} · {job.scenes?.length || 0} scenes
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <Badge status={job.status} />
                    <Link to={`/studio/scenes/${job.jobId}`} style={{ textDecoration: "none" }}>
                      <Button>Edit scenes</Button>
                    </Link>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
      </section>

      <section>
        <h3 className="section-title" style={{ margin: "0 0 8px" }}>
          Studio outputs
        </h3>
        {completedRuns.length === 0 ? (
          <EmptyState
            icon="🖼️"
            title="No Studio outputs yet"
            hint="Images and clips made in Tools can be sent straight to the timeline."
          />
        ) : (
          <div style={{ display: "grid", gap: 8 }}>
            {completedRuns.map((run) => (
              <Card key={run.run_id}>
                <div className="spread" style={{ gap: 10, flexWrap: "wrap" }}>
                  <div style={{ minWidth: 200 }}>
                    <div style={{ fontWeight: 700 }}>{run.title || run.prompt || "Untitled"}</div>
                    <div className="faint" style={{ fontSize: "0.8rem" }}>
                      {run.tool} · {run.model} · {when(run.created_at)}
                    </div>
                  </div>
                  <Button variant="primary" onClick={() => openRun(run)}>
                    Use in video
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
