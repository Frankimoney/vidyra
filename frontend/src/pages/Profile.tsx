import { useEffect, useState } from "react";
import { signOut } from "firebase/auth";
import { auth } from "../firebase";
import { useAuth } from "../contexts/AuthContext";
import { useJobs } from "../hooks/useJobs";
import { Button, Card } from "../components/ui";
import { api } from "../services/api";
import { useNavigate, Link } from "react-router-dom";
import { BoltIcon } from "../components/layout/AppLayout";

export default function Profile() {
  const { user, pageName } = useAuth();
  const { jobs } = useJobs();
  const navigate = useNavigate();

  const [workspacePage, setWorkspacePage] = useState("");

  useEffect(() => {
    api
      .listWorkspaces()
      .then((r) => setWorkspacePage(r.items?.[0]?.page_name || ""))
      .catch(() => setWorkspacePage(""));
  }, []);

  const completed = jobs.filter((j) => j.status === "completed").length;
  const failed = jobs.filter((j) => j.status === "failed").length;

  const now = Date.now();
  const weekMs = 7 * 24 * 3600 * 1000;
  const renderWeek = jobs.filter(
    (j) => now - new Date(j.createdAt).getTime() < weekMs
  ).length;
  const storageMb = jobs
    .reduce((sum, j) => sum + (j.output?.fileSize || 0), 0)
    / (1024 * 1024);

  const initial = user?.email?.[0]?.toUpperCase() || "V";

  async function logout() {
    await signOut(auth);
    navigate("/login");
  }

  return (
    <div className="stack-v fade-up">
      <h2 style={{ marginTop: 0 }}>Account</h2>

      <Card style={{ textAlign: "center" }}>
        <div className="avatar" style={{ margin: "0 auto 12px" }}>{initial}</div>
        <div style={{ fontWeight: 700, fontSize: "1.1rem" }}>
          {user?.displayName || "Vidyra Creator"}
        </div>
        <div className="faint" style={{ fontSize: "0.9rem" }}>{user?.email}</div>
        {pageName && (
          <div
            className="badge"
            style={{
              marginTop: 10,
              background: "rgba(139,92,246,0.16)",
              color: "var(--primary-strong)",
            }}
          >
            {`@${pageName}`}
          </div>
        )}
      </Card>

      <Card>
        <div className="spread" style={{ gap: 10, flexWrap: "wrap" }}>
          <div>
            <div style={{ fontWeight: 700 }}>Page name</div>
            <p className="faint" style={{ margin: "2px 0 0", fontSize: "0.82rem" }}>
              Set once in Workspace so every video inherits the same brand.
            </p>
          </div>
          <Link to="/workspace" style={{ color: "var(--accent)", fontSize: "0.86rem" }}>
            Open Workspace →
          </Link>
        </div>
        {(workspacePage || pageName) && (
          <div
            className="badge"
            style={{
              marginTop: 10,
              background: "rgba(139,92,246,0.16)",
              color: "var(--primary-strong)",
            }}
          >
            @{workspacePage || pageName}
          </div>
        )}
      </Card>

      <div className="grid-2">
        <Card style={{ textAlign: "center" }}>
          <div style={{ fontSize: "1.8rem", fontWeight: 800 }}>{completed}</div>
          <div className="muted" style={{ fontSize: "0.8rem" }}>Completed</div>
        </Card>
        <Card style={{ textAlign: "center" }}>
          <div style={{ fontSize: "1.8rem", fontWeight: 800, color: "var(--accent)" }}>
            {renderWeek}
          </div>
          <div className="muted" style={{ fontSize: "0.8rem" }}>Renders this week</div>
        </Card>
        <Card style={{ textAlign: "center" }}>
          <div style={{ fontSize: "1.8rem", fontWeight: 800, color: "var(--danger)" }}>
            {failed}
          </div>
          <div className="muted" style={{ fontSize: "0.8rem" }}>Failed</div>
        </Card>
        <Card style={{ textAlign: "center" }}>
          <div style={{ fontSize: "1.8rem", fontWeight: 800, color: "var(--primary-strong)" }}>
            {storageMb < 1 ? "<1" : storageMb.toFixed(0)}
            <span className="muted" style={{ fontSize: "1rem" }}> MB</span>
          </div>
          <div className="muted" style={{ fontSize: "0.8rem" }}>Videos stored</div>
        </Card>
      </div>

      <div className="card">
        <div className="spread" style={{ padding: "6px 0" }}>
          <span>Vidyra</span>
          <span className="faint">
            <BoltIcon size={14} /> v0.1.0
          </span>
        </div>
        <div className="divider" />
        <div className="spread" style={{ padding: "6px 0" }}>
          <span>Processing backend</span>
          <span className="faint">Oracle Cloud</span>
        </div>
        <div className="divider" />
        <div
          className="spread"
          style={{ padding: "6px 0", fontSize: "0.9rem", gap: 10, flexWrap: "wrap", justifyContent: "center" }}
        >
          <Link to="/privacy" style={{ color: "var(--text-2)", textDecoration: "none" }}>Privacy Policy</Link>
          <Link to="/terms" style={{ color: "var(--text-2)", textDecoration: "none" }}>Terms of Service</Link>
          <Link to="/about" style={{ color: "var(--text-2)", textDecoration: "none" }}>About</Link>
        </div>
      </div>

      <Button variant="ghost" onClick={logout}>Sign out</Button>
    </div>
  );
}