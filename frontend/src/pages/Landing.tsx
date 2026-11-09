import { Link } from "react-router-dom";
import { Button, Card } from "../components/ui";
import { BoltIcon } from "../components/layout/AppLayout";

const FEATURES = [
  { icon: "🎙️", title: "Script → voice", text: "Type a story and hear it narrated instantly by real-sounding AI voices." },
  { icon: "🎬", title: "Auto visual editing", text: "Clips, Ken Burns motion, transitions and music are handled for you." },
  { icon: "💬", title: "Karaoke captions", text: "Big word-by-word captions timed to the narration, styled to your brand." },
  { icon: "📱", title: "Ready for Shorts", text: "Posts render as 9:16 vertical video with your page name built in." },
];

export default function Landing() {
  return (
    <div className="fade-up">
      <header className="spread" style={{ marginBottom: 24 }}>
        <div className="logo">
          <BoltIcon size={20} />
          vi<span className="bolt">dyra</span>
        </div>
        <Link to="/login" style={{ fontSize: "0.9rem", fontWeight: 600, color: "var(--accent)", textDecoration: "none" }}>
          Sign in
        </Link>
      </header>

      <section className="hero" style={{ textAlign: "center" }}>
        <h2 style={{ fontSize: "2rem" }}>
          Turn any idea into a finished <span className="bolt">video</span>
        </h2>
        <p className="tagline">
          Script → voice → visuals → captions → a ready 9:16 video. AI films,
          podcasts, and talking-head clips, all from one place.
        </p>
        <div className="btn-row" style={{ marginTop: 22 }}>
          <Link to="/login?mode=signup" style={{ flex: 1, textDecoration: "none" }}>
            <Button variant="primary" size="lg">Get started free</Button>
          </Link>
        </div>
        <p className="faint" style={{ fontSize: "0.8rem", marginTop: 12 }}>
          No credit card · Create your page name during signup
        </p>
      </section>

      <h3 className="section-title">What you get</h3>
      <div className="stack-v">
        {FEATURES.map((f) => (
          <Card key={f.title}>
            <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
              <div style={{ fontSize: 26 }}>{f.icon}</div>
              <div>
                <div style={{ fontWeight: 800 }}>{f.title}</div>
                <p className="muted" style={{ margin: "4px 0 0", fontSize: "0.9rem" }}>{f.text}</p>
              </div>
            </div>
          </Card>
        ))}
      </div>

      <div className="card" style={{ textAlign: "center", marginTop: 16 }}>
        <div style={{ fontWeight: 800, fontSize: "1.1rem" }}>Your name on every video</div>
        <p className="muted" style={{ fontSize: "0.9rem", margin: "6px 0 0" }}>
          Your page name appears as a brand badge on every clip you make.
        </p>
      </div>

      <footer
        style={{
          marginTop: 30,
          display: "flex",
          justifyContent: "center",
          gap: 18,
          fontSize: "0.8rem",
        }}
      >
        <Link to="/about" style={{ color: "var(--text-3)", textDecoration: "none" }}>About</Link>
        <Link to="/privacy" style={{ color: "var(--text-3)", textDecoration: "none" }}>Privacy</Link>
        <Link to="/terms" style={{ color: "var(--text-3)", textDecoration: "none" }}>Terms</Link>
      </footer>
    </div>
  );
}