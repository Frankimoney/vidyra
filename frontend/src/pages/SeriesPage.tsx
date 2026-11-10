import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useJobs } from "../hooks/useJobs";
import { api } from "../services/api";
import { Button, Card, EmptyState, Field, Spinner } from "../components/ui";
import { useToast } from "../components/feedback";
import type { JobItem, Series } from "../types";

function when(iso: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}

export default function SeriesPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [series, setSeries] = useState<Series[]>([]);
  const { jobs: allJobs } = useJobs();
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [jobId, setJobId] = useState("");
  const [name, setName] = useState("");
  const [hours, setHours] = useState(24);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");

  useEffect(() => {
    api.listSeries().then((s) => setSeries(s.items || [])).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const completed = allJobs.filter((x) => x.status === "completed");
    setJobs(completed);
    if (!jobId && completed[0]) {
      setJobId(completed[0].jobId);
      setName(completed[0].title || "Recurring video");
    }
  }, [allJobs, jobId]);

  async function create() {
    const job = jobs.find((j) => j.jobId === jobId);
    if (!job) {
      toast("Pick a finished video to use as the template.", "error");
      return;
    }
    setBusy("create");
    try {
      const created = await api.createSeries({
        name: name.trim() || job.title,
        interval_hours: Math.max(1, Math.round(hours)),
        workspace_id: job.workspaceId || "",
        payload: {
          title: job.title,
          script: job.script,
          media: job.media,
          settings: job.settings || {},
          cover: job.cover || null,
          niche: job.niche || "",
          scenes: job.scenes || [],
          characters: job.characters || [],
        },
      });
      setSeries((prev) => [...prev, created]);
      toast("Series created. It will re-render on schedule.", "success");
    } catch (e) {
      toast((e as Error).message || "Could not create the series.", "error");
    } finally {
      setBusy("");
    }
  }

  async function toggle(item: Series) {
    setBusy(item.series_id);
    try {
      const updated = await api.updateSeries(item.series_id, { enabled: !item.enabled });
      setSeries((prev) => prev.map((s) => (s.series_id === item.series_id ? updated : s)));
    } catch (e) {
      toast((e as Error).message || "Could not update the series.", "error");
    } finally {
      setBusy("");
    }
  }

  async function runNow(item: Series) {
    setBusy(item.series_id);
    try {
      const res = await api.runSeries(item.series_id);
      navigate(`/job/${res.job_id}`);
    } catch (e) {
      toast((e as Error).message || "Could not start the series.", "error");
    } finally {
      setBusy("");
    }
  }

  async function remove(item: Series) {
    setBusy(item.series_id);
    try {
      await api.deleteSeries(item.series_id);
      setSeries((prev) => prev.filter((s) => s.series_id !== item.series_id));
      toast("Series deleted.", "success");
    } catch (e) {
      toast((e as Error).message || "Could not delete the series.", "error");
    } finally {
      setBusy("");
    }
  }

  if (loading) {
    return (
      <div style={{ display: "grid", placeItems: "center", minHeight: "40dvh" }}>
        <Spinner />
      </div>
    );
  }

  return (
    <div className="fade-up" style={{ paddingBottom: 28 }}>
      <header style={{ marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>Series</h2>
        <p className="faint" style={{ fontSize: "0.86rem", margin: "2px 0 0" }}>
          Re-render a finished video template on a schedule. Generation only —
          publishing stays manual.
        </p>
      </header>

      <div style={{ display: "grid", gap: 14, gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
        <Card>
          <h3 className="section-title" style={{ marginTop: 0 }}>New series</h3>
          <Field label="Template video">
            <select className="input" value={jobId} onChange={(e) => {
              setJobId(e.target.value);
              const job = jobs.find((j) => j.jobId === e.target.value);
              if (job) setName(job.title);
            }}>
              {jobs.map((j) => (
                <option key={j.jobId} value={j.jobId}>{j.title}</option>
              ))}
            </select>
          </Field>
          <Field label="Series name">
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field label="Every (hours)">
            <input className="input" type="number" min={1} max={720} value={hours} onChange={(e) => setHours(Number(e.target.value) || 24)} />
          </Field>
          <Button variant="primary" onClick={create} loading={busy === "create"} disabled={!jobs.length}>
            Create series
          </Button>
          {!jobs.length && (
            <p className="faint" style={{ fontSize: "0.8rem", marginBottom: 0 }}>
              Render a video first — series reuse a finished template.
            </p>
          )}
        </Card>

        <Card>
          <h3 className="section-title" style={{ marginTop: 0 }}>How it works</h3>
          <p className="faint" style={{ fontSize: "0.84rem", marginTop: 0 }}>
            The scheduler checks every minute. When a series is due it creates a
            new job with the saved scenes, media and settings. Costs the same GPU
            runs as a normal render.
          </p>
        </Card>
      </div>

      <h3 className="section-title" style={{ margin: "24px 0 10px" }}>
        Your series ({series.length})
      </h3>

      {series.length === 0 ? (
        <EmptyState icon="🔁" title="No series yet" hint="Create one from a finished video above." />
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          {series.map((item) => (
            <Card key={item.series_id}>
              <div className="spread" style={{ gap: 10, flexWrap: "wrap" }}>
                <div style={{ minWidth: 180 }}>
                  <div style={{ fontWeight: 800 }}>{item.name}</div>
                  <div className="faint" style={{ fontSize: "0.8rem" }}>
                    Every {item.interval_hours}h · next {when(item.next_run_at)}
                  </div>
                </div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                  <Button onClick={() => toggle(item)} loading={busy === item.series_id}>
                    {item.enabled ? "Pause" : "Resume"}
                  </Button>
                  <Button variant="primary" onClick={() => runNow(item)} loading={busy === item.series_id}>
                    Run now
                  </Button>
                  <Button variant="ghost" onClick={() => remove(item)} disabled={busy === item.series_id}>
                    Delete
                  </Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
