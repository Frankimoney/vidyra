import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { publicApi } from "../services/api";
import { Button, Spinner } from "../components/ui";
import type { PublicShare } from "../types";

export default function ShareWatch() {
  const { token = "" } = useParams();
  const [data, setData] = useState<PublicShare | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    publicApi
      .watch(token)
      .then((d) => alive && setData(d))
      .catch(() => alive && setError("This video is no longer available."))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [token]);

  if (loading) {
    return (
      <div style={{ display: "grid", placeItems: "center", minHeight: "100dvh" }}>
        <Spinner />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div
        style={{
          minHeight: "100dvh",
          display: "grid",
          placeItems: "center",
          padding: 24,
          textAlign: "center",
        }}
      >
        <div>
          <div style={{ fontSize: 40 }}>🔍</div>
          <h2 style={{ margin: "10px 0 4px" }}>Video not found</h2>
          <p className="muted" style={{ fontSize: "0.9rem" }}>{error}</p>
          <div style={{ marginTop: 16 }}>
            <Link to="/" style={{ textDecoration: "none" }}>
              <Button variant="ghost">Go to Vidyra</Button>
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      style={{
        minHeight: "100dvh",
        maxWidth: 480,
        margin: "0 auto",
        padding: "24px 18px",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <header className="spread" style={{ marginBottom: 16 }}>
        <div className="logo" style={{ fontSize: "1.05rem" }}>
          <span style={{ fontWeight: 800 }}>
            vi<span className="bolt">dyra</span>
          </span>
        </div>
        {data.pageName && (
          <span
            className="badge"
            style={{
              background: "rgba(139,92,246,0.16)",
              color: "var(--primary-strong)",
            }}
          >
            {`@${data.pageName}`}
          </span>
        )}
      </header>

      <div className="thumb" style={{ width: "82%", margin: "0 auto" }}>
        <video
          src={data.url}
          poster={data.poster || undefined}
          controls
          playsInline
          style={{ width: "100%", height: "100%" }}
        />
      </div>

      <div style={{ textAlign: "center", marginTop: 18 }}>
        <div style={{ fontWeight: 800, fontSize: "1.15rem" }}>{data.title}</div>
        {data.duration ? (
          <div className="muted" style={{ fontSize: "0.85rem", marginTop: 4 }}>
            {Math.round(data.duration)}s vertical video
          </div>
        ) : null}
      </div>

      <div style={{ marginTop: 20 }}>
        <a href={data.url} download style={{ textDecoration: "none" }}>
          <Button variant="primary">Download MP4</Button>
        </a>
      </div>

      <footer style={{ marginTop: "auto", paddingTop: 30, textAlign: "center" }}>
        <span className="faint" style={{ fontSize: "0.8rem" }}>
          Made with Vidyra — turn your sports stories into videos
        </span>
        <div style={{ marginTop: 6 }}>
          <Link to="/" style={{ color: "var(--accent)", fontSize: "0.8rem", textDecoration: "none" }}>
            Create your own
          </Link>
        </div>
      </footer>
    </div>
  );
}