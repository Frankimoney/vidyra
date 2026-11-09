import { useEffect, useState } from "react";
import { api } from "../../services/api";
import { Card } from "../ui";
import type { UsageSummary } from "../../types";

function money(value: number): string {
  if (!value) return "$0.00";
  return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}

export function UsageCard({
  workspaceId,
  dailyRuns = 0,
  days = 30,
  compact = false,
  refreshKey = 0,
}: {
  workspaceId?: string;
  dailyRuns?: number;
  days?: number;
  compact?: boolean;
  refreshKey?: number;
}) {
  const [data, setData] = useState<UsageSummary | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .usageSummary(workspaceId, days)
      .then((r) => {
        if (alive) {
          setData(r);
          setError("");
        }
      })
      .catch((e: Error) => {
        if (alive) setError(e.message || "Usage unavailable.");
      });
    return () => {
      alive = false;
    };
  }, [workspaceId, days, refreshKey]);

  if (error) {
    return (
      <Card>
        <p className="faint" style={{ margin: 0, fontSize: "0.82rem" }}>
          {error}
        </p>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card>
        <p className="faint" style={{ margin: 0, fontSize: "0.82rem" }}>
          Loading usage…
        </p>
      </Card>
    );
  }

  const capped = dailyRuns > 0;
  const pct = capped ? Math.min(100, (data.today_runs / dailyRuns) * 100) : 0;

  return (
    <Card>
      <div className="spread" style={{ alignItems: "baseline", gap: 10 }}>
        <div>
          <div className="faint" style={{ fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.08em" }}>
            GPU usage
          </div>
          <div style={{ fontWeight: 800, fontSize: compact ? "1.1rem" : "1.4rem" }}>
            {data.today_runs} run{data.today_runs === 1 ? "" : "s"} today
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div className="faint" style={{ fontSize: "0.72rem" }}>
            Last {data.days} days
          </div>
          <div style={{ fontWeight: 700 }}>{money(data.estimated_cost)}</div>
        </div>
      </div>

      <div style={{ marginTop: 10 }}>
        <div className="spread faint" style={{ fontSize: "0.76rem", marginBottom: 4 }}>
          <span>{capped ? `Daily cap ${dailyRuns} runs` : "No daily cap"}</span>
          <span>
            {data.runs} run{data.runs === 1 ? "" : "s"} in {data.days}d
          </span>
        </div>
        <div className="progress-track">
          <div
            className="progress-fill"
            style={{
              width: `${capped ? pct : Math.min(100, data.runs * 4)}%`,
              background: capped && pct >= 90 ? "var(--danger)" : undefined,
              opacity: capped ? 1 : 0.5,
            }}
          />
        </div>
      </div>

      {!compact && data.by_model.length > 0 && (
        <div style={{ marginTop: 14, display: "grid", gap: 6 }}>
          {data.by_model.slice(0, 6).map((m) => (
            <div key={m.model} className="spread" style={{ fontSize: "0.82rem" }}>
              <span className="faint">{m.model}</span>
              <span>
                {m.runs} · {money(m.cost)}
              </span>
            </div>
          ))}
        </div>
      )}

      {!compact && data.by_model.length === 0 && (
        <p className="faint" style={{ margin: "10px 0 0", fontSize: "0.82rem" }}>
          Nothing rendered yet. Drafts are always free.
        </p>
      )}
    </Card>
  );
}
