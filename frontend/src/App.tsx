import React from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./contexts/AuthContext";
import { AppLayout } from "./components/layout/AppLayout";

class ErrorBoundary extends React.Component<
  { children: React.ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error) {
    console.error("[Vidyra] UI crash:", error);
  }
  render() {
    if (this.state.error) {
      return (
        <div
          style={{
            minHeight: "100dvh",
            display: "grid",
            placeItems: "center",
            background: "var(--bg)",
            padding: 24,
            textAlign: "center",
          }}
        >
          <div>
            <h2>Something went wrong.</h2>
            <p className="faint" style={{ maxWidth: 420 }}>
              {(this.state.error as Error).message || "A render error occurred."}
            </p>
            <button
              className="btn"
              onClick={() => {
                this.setState({ error: null });
                window.location.href = "/studio";
              }}
            >
              Back to Studio
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
import Login from "./pages/Login";
import Landing from "./pages/Landing";
import StudioLayout from "./pages/StudioLayout";
import StudioCreate from "./pages/StudioCreate";
import StudioScenes from "./pages/StudioScenes";
import Studio from "./pages/Studio";
import Characters from "./pages/Characters";
import Editor from "./pages/Editor";
import Library from "./pages/Library";
import Workspace from "./pages/Workspace";
import Profile from "./pages/Profile";
import JobStatus from "./pages/JobStatus";
import ShareWatch from "./pages/ShareWatch";
import { About, Privacy, Terms } from "./pages/LegalPages";
import { Spinner } from "./components/ui";

function RootGate() {
  const { ready } = useAuth();
  if (!ready) return <Landing />;
  return <Navigate to="/studio" replace />;
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { ready, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div
        style={{
          minHeight: "100dvh",
          display: "grid",
          placeItems: "center",
          background: "var(--bg)",
        }}
      >
        <Spinner />
      </div>
    );
  }

  if (!ready) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}

export default function App() {
  return (
    <ErrorBoundary>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/v/:token" element={<ShareWatch />} />
        <Route path="/about" element={<About />} />
        <Route path="/privacy" element={<Privacy />} />
        <Route path="/terms" element={<Terms />} />

        <Route path="/" element={<RootGate />} />

        <Route
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        >
          <Route path="/studio" element={<StudioLayout />}>
            <Route index element={<Navigate to="create" replace />} />
            <Route path="create" element={<StudioCreate />} />
            <Route path="scenes" element={<StudioScenes />} />
            <Route path="scenes/editor" element={<Editor />} />
            <Route path="scenes/:jobId" element={<Editor />} />
            <Route path="tools" element={<Studio />} />
            <Route path="cast" element={<Characters />} />
          </Route>

          <Route path="/library" element={<Library />} />
          <Route path="/workspace" element={<Workspace />} />
          <Route path="/profile" element={<Profile />} />
          <Route path="/job/:jobId" element={<JobStatus />} />

          <Route path="/create" element={<Navigate to="/studio/create" replace />} />
          <Route path="/new" element={<Navigate to="/studio/create" replace />} />
          <Route path="/film" element={<Navigate to="/studio/create" replace />} />
          <Route path="/characters" element={<Navigate to="/studio/cast" replace />} />
          <Route path="/videos" element={<Navigate to="/library" replace />} />
          <Route path="/series" element={<Navigate to="/studio/create" replace />} />
          <Route path="/storyboard" element={<Navigate to="/studio/scenes" replace />} />
          <Route path="/editor" element={<Navigate to="/studio/scenes" replace />} />
          <Route path="/editor/:jobId" element={<Editor />} />
          <Route path="/edit/:jobId" element={<Editor />} />
          <Route path="/account" element={<Navigate to="/profile" replace />} />
          <Route path="/user" element={<Navigate to="/profile" replace />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </ErrorBoundary>
  );
}
