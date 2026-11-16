import { Link } from "react-router-dom";
import { Button, Card } from "../components/ui";
import { BoltIcon } from "../components/layout/AppLayout";

const FEATURES = [
  {
    icon: "🎬",
    title: "AI Director & Auto Film",
    text: "Plan multi-scene stories with locked character faces, camera moves, and grounded facts before spending GPU runs.",
  },
  {
    icon: "🎙️",
    title: "Studio Voice & Clone",
    text: "Narrate with expressive voices or clone your own voice so every character and host sounds unmistakably real.",
  },
  {
    icon: "✨",
    title: "Motion & Visual Tools",
    text: "Turn stills into living shots, restyle references, and lip-sync speaking characters on serverless GPUs.",
  },
  {
    icon: "💬",
    title: "Karaoke Captions & Brand",
    text: "Word-by-word captions, custom color palettes, music ducking, and your @page badge baked into every render.",
  },
];

const WORKFLOW_STEPS = [
  { step: "01", label: "Brief the Director", detail: "Type a one-line idea or paste a full production script." },
  { step: "02", label: "Approve the Plan", detail: "Review every scene, character face, and camera shot upfront." },
  { step: "03", label: "Render & Share", detail: "Download a ready-to-post vertical or widescreen MP4 in minutes." },
];

export default function Landing() {
  return (
    <div className="fade-up" style={{ maxWidth: 960, margin: "0 auto", padding: "8px 0 36px" }}>
      <header className="spread" style={{ marginBottom: 28 }}>
        <div className="logo" style={{ fontSize: "1.35rem" }}>
          <BoltIcon size={22} />
          vi<span className="bolt">dyra</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Link
            to="/login"
            className="btn btn-ghost"
            style={{ width: "auto", padding: "9px 16px", fontSize: "0.88rem", textDecoration: "none" }}
          >
            Sign in
          </Link>
          <Link
            to="/login?mode=signup"
            className="btn btn-primary"
            style={{ width: "auto", padding: "9px 16px", fontSize: "0.88rem", textDecoration: "none" }}
          >
            Start creating
          </Link>
        </div>
      </header>

      <section className="hero" style={{ textAlign: "center", padding: "48px 28px" }}>
        <div className="pill-badge" style={{ marginBottom: 16 }}>
          <BoltIcon size={13} /> AI Video Production Studio
        </div>
        <h2>
          Turn any idea into a finished <span className="bolt">video</span>
        </h2>
        <p className="tagline">
          Script → voice → visuals → karaoke captions → a broadcast-ready video.
          Direct AI films, talking-head clips, and narrated stories from one studio.
        </p>
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            gap: 12,
            flexWrap: "wrap",
            maxWidth: 440,
            margin: "24px auto 0",
          }}
        >
          <Link to="/login?mode=signup" style={{ flex: "1 1 200px", textDecoration: "none" }}>
            <Button variant="primary" size="lg">
              Get started free
            </Button>
          </Link>
          <Link to="/login" style={{ flex: "1 1 140px", textDecoration: "none" }}>
            <Button variant="ghost" size="lg">
              Sign in
            </Button>
          </Link>
        </div>
        <p className="faint" style={{ fontSize: "0.8rem", marginTop: 14, marginBottom: 0 }}>
          No credit card required · Lock your brand &amp; cast once, reuse on every video
        </p>
      </section>

      <h3 className="section-title" style={{ marginTop: 32 }}>
        Everything in one studio
      </h3>
      <div className="feature-grid">
        {FEATURES.map((f) => (
          <Card key={f.title}>
            <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
              <div className="feature-icon-box">{f.icon}</div>
              <div>
                <div style={{ fontWeight: 800, fontSize: "0.98rem" }}>{f.title}</div>
                <p className="muted" style={{ margin: "6px 0 0", fontSize: "0.88rem", lineHeight: 1.5 }}>
                  {f.text}
                </p>
              </div>
            </div>
          </Card>
        ))}
      </div>

      <h3 className="section-title" style={{ marginTop: 32 }}>
        How it works
      </h3>
      <div className="feature-grid">
        {WORKFLOW_STEPS.map((s) => (
          <Card key={s.step}>
            <div
              style={{
                fontFamily: "var(--font-display)",
                fontSize: "0.76rem",
                fontWeight: 800,
                color: "var(--accent)",
                letterSpacing: "0.08em",
              }}
            >
              STEP {s.step}
            </div>
            <div style={{ fontWeight: 800, fontSize: "1rem", marginTop: 4 }}>{s.label}</div>
            <p className="muted" style={{ margin: "6px 0 0", fontSize: "0.86rem", lineHeight: 1.5 }}>
              {s.detail}
            </p>
          </Card>
        ))}
      </div>

      <div
        className="card"
        style={{
          textAlign: "center",
          marginTop: 24,
          background: "var(--gradient-subtle)",
          borderColor: "rgba(167, 139, 250, 0.28)",
        }}
      >
        <div style={{ fontWeight: 800, fontSize: "1.12rem" }}>Your brand on every frame</div>
        <p className="muted" style={{ fontSize: "0.9rem", margin: "6px auto 0", maxWidth: 520 }}>
          Set your page name, colors, and default voice once in Workspace — every new film and clip inherits them automatically.
        </p>
      </div>

      <footer
        style={{
          marginTop: 36,
          display: "flex",
          justifyContent: "center",
          gap: 22,
          fontSize: "0.82rem",
        }}
      >
        <Link to="/about" style={{ color: "var(--text-3)", textDecoration: "none" }}>
          About
        </Link>
        <Link to="/privacy" style={{ color: "var(--text-3)", textDecoration: "none" }}>
          Privacy
        </Link>
        <Link to="/terms" style={{ color: "var(--text-3)", textDecoration: "none" }}>
          Terms
        </Link>
      </footer>
    </div>
  );
}