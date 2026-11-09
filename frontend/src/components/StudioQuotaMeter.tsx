import { useEffect, useState } from "react";
import { api } from "../services/api";
import type { StudioQuota } from "../services/api";

/**
 * Live "X of Y runs used today" meter for the daily AI Studio budget.
 * Purely informational — the backend still enforces the ceiling before any
 * GPU run is queued. Polls every 20s so a finished film visibly drains it.
 */
export function StudioQuotaMeter({ compact }: { compact?: boolean }) {
  const [quota, setQuota] = useState<StudioQuota | null>(null);
  const [unsupported, setUnsupported] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .studioQuota()
        .then((q) => {
          if (!alive) return;
          setQuota(q);
          setUnsupported(false);
        })
        .catch(() => {
          /* older backend without the quota endpoint -> show nothing */
          if (alive) setUnsupported(true);
        });
    load();
    const t = window.setInterval(load, 20000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  if (unsupported || !quota) return null;
  const low = quota.remaining <= 3;
  return (
    <div
      title={`${quota.used} of ${quota.max} daily AI Studio runs used`}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: "0.76rem",
        padding: "4px 8px",
        borderRadius: 999,
        background: low ? "rgba(248,113,113,0.15)" : "rgba(16,185,129,0.12)",
        color: low ? "var(--danger, #f87171)" : "var(--text)",
        whiteSpace: "nowrap",
      }}
    >
      <span>☁️ {quota.used}/{quota.max} runs today</span>
      {!compact &&
        (quota.remaining > 0 ? (
          <span
            className="faint"
            style={{ color: low ? "var(--danger, #f87171)" : undefined }}
          >
            · {quota.remaining} left
          </span>
        ) : (
          <span>· cap reached</span>
        ))}
    </div>
  );
}