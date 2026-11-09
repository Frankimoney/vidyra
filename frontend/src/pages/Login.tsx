import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  signInWithPopup,
  sendPasswordResetEmail,
  GoogleAuthProvider,
} from "firebase/auth";
import { auth } from "../firebase";
import { api } from "../services/api";
import { Button, Field } from "../components/ui";
import { useToast } from "../components/feedback";

export default function Login() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [mode, setMode] = useState<"signin" | "signup">(
    params.get("mode") === "signup" ? "signup" : "signin"
  );
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pageName, setPageName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (mode === "signup") {
        await createUserWithEmailAndPassword(auth, email, password);
        const name = pageName.trim();
        if (name) {
          try {
            await api.saveProfile(name);
          } catch {
            toast("Account created — add your page name in Profile later.", "info");
          }
        }
      } else {
        await signInWithEmailAndPassword(auth, email, password);
      }
      navigate("/");
    } catch (err) {
      setError((err as Error).message || "Authentication failed.");
    } finally {
      setBusy(false);
    }
  }

  async function google() {
    setError("");
    setBusy(true);
    try {
      await signInWithPopup(auth, new GoogleAuthProvider());
      navigate("/");
    } catch (err) {
      setError((err as Error).message || "Google sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  async function resetPassword() {
    setError("");
    if (!email.trim()) {
      setError("Enter your email above first.");
      return;
    }
    setBusy(true);
    try {
      await sendPasswordResetEmail(auth, email);
      toast("Reset email sent — check your inbox.", "success");
    } catch (err) {
      setError((err as Error).message || "Could not send reset email.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100dvh",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        padding: 24,
        maxWidth: 480,
        margin: "0 auto",
      }}
    >
      <div style={{ marginBottom: 28, textAlign: "center" }}>
        <div
          className="logo"
          style={{ fontSize: "2.2rem", justifyContent: "center", width: "100%" }}
        >
          vi<span className="bolt">dyra</span>
        </div>
        <p className="muted" style={{ margin: "10px 0 0", fontSize: "0.95rem" }}>
          Turn sports stories into videos
        </p>
      </div>

      <div className="card fade-up">
        <div className="segmented" style={{ marginBottom: 16 }}>
          <button
            type="button"
            className={`seg-btn ${mode === "signin" ? "active" : ""}`}
            onClick={() => setMode("signin")}
          >
            Sign In
          </button>
          <button
            type="button"
            className={`seg-btn ${mode === "signup" ? "active" : ""}`}
            onClick={() => setMode("signup")}
          >
            Create Account
          </button>
        </div>

        <form onSubmit={submit} className="stack-v">
          <input
            className="input"
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <input
            className="input"
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={6}
          />
          {mode === "signup" && (
            <Field label="Page name (shows on your videos)">
              <input
                className="input"
                type="text"
                placeholder="e.g. GoalDiggers"
                value={pageName}
                onChange={(e) => setPageName(e.target.value)}
                maxLength={30}
              />
            </Field>
          )}
          <button
            type="button"
            onClick={resetPassword}
            disabled={busy}
            style={{
              alignSelf: "flex-end",
              background: "none",
              border: "none",
              padding: 0,
              color: "var(--accent)",
              fontSize: "0.82rem",
              cursor: "pointer",
            }}
          >
            Forgot password?
          </button>
          {error && (
            <div style={{ color: "var(--danger)", fontSize: "0.86rem" }}>
              {error}
            </div>
          )}
          <Button variant="primary" type="submit" loading={busy}>
            {mode === "signin" ? "Sign In" : "Create Account"}
          </Button>
        </form>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            margin: "18px 0",
            color: "var(--text-3)",
            fontSize: "0.8rem",
          }}
        >
          <div className="divider" style={{ flex: 1, margin: 0 }} />
          or
          <div className="divider" style={{ flex: 1, margin: 0 }} />
        </div>

        <Button className="btn-ghost" onClick={google} loading={busy}>
          Continue with Google
        </Button>
      </div>

      <footer className="faint" style={{ marginTop: 18, textAlign: "center", fontSize: "0.78rem" }}>
        By continuing you agree to our{" "}
        <a href="/terms" style={{ color: "var(--accent)" }}>Terms</a> and{" "}
        <a href="/privacy" style={{ color: "var(--accent)" }}>Privacy Policy</a>.
      </footer>
    </div>
  );
}