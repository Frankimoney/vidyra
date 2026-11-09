import { Link } from "react-router-dom";
import { BoltIcon } from "../components/layout/AppLayout";

function Page({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100dvh",
        maxWidth: 560,
        margin: "0 auto",
        padding: "28px 20px",
      }}
      className="fade-up"
    >
      <Link to="/" style={{ textDecoration: "none" }}>
        <div className="logo" style={{ marginBottom: 22 }}>
          <BoltIcon size={18} />
          vi<span className="bolt">dyra</span>
        </div>
      </Link>
      <h2 style={{ marginTop: 0 }}>{title}</h2>
      <div className="card">
        <div style={{ fontSize: "0.92rem", lineHeight: 1.6, color: "var(--text-2)" }}>
          {children}
        </div>
      </div>
      <footer style={{ marginTop: 22, textAlign: "center", fontSize: "0.8rem" }}>
        <Link to="/" style={{ color: "var(--accent)", textDecoration: "none" }}>← Back to Vidyra</Link>
      </footer>
    </div>
  );
}

export function About() {
  return (
    <Page title="About Vidyra">
      <p>
        Vidyra turns a sports story into a ready-to-post vertical video: an AI voice
        reads your script while your photos and clips are edited into a 9:16 video with
        captions, music, transitions, and your page name built in.
      </p>
      <p>
        This is an early release. New voices, music packs, and editing tools land
        regularly.
      </p>
    </Page>
  );
}

export function Privacy() {
  return (
    <Page title="Privacy Policy">
      <p>
        Vidyra stores the account details you provide (email and optional page name),
        the scripts and media you upload, and the videos you generate. Your content is
        private to your account unless you share a link.
      </p>
      <p>
        Generated videos you share are readable by anyone with the link for up to 7 days.
        We do not sell your data. Files are deleted when you delete a video.
      </p>
      <p>
        Questions? Contact us through the app.
      </p>
    </Page>
  );
}

export function Terms() {
  return (
    <Page title="Terms of Service">
      <p>
        By using Vidyra you agree to use the service lawfully and to not upload content
        you do not have the right to use. You own the videos you create.
      </p>
      <p>
        Videos are generated with automated tools; we make no guarantee about output
        accuracy. The free tier includes a daily render limit.
      </p>
    </Page>
  );
}