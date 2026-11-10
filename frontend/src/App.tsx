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
                window.location.href = "/";
              }}
            >
              Back to home
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
import Login from "./pages/Login";
import Home from "./pages/Home";
import Landing from "./pages/Landing";
import NewVideo from "./pages/NewVideo";
import Workspace from "./pages/Workspace";
import Studio from "./pages/Studio";
import SeriesPage from "./pages/SeriesPage";
import Film from "./pages/Film";
import Characters from "./pages/Characters";
import Editor from "./pages/Editor";
import MyVideos from "./pages/MyVideos";
import Profile from "./pages/Profile";
import JobStatus from "./pages/JobStatus";
import ShareWatch from "./pages/ShareWatch";
import { About, Privacy, Terms } from "./pages/LegalPages";
import { Spinner } from "./components/ui";

function RootGate() {
  const { ready } = useAuth();
  if (!ready) return <Landing />;
  return <AppLayout />;
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

      <Route path="/" element={<RootGate />}>
        <Route index element={<Home />} />
      </Route>

      <Route
        element={
          <RequireAuth>
            <AppLayout />
          </RequireAuth>
        }
      >
        <Route path="/new" element={<NewVideo />} />
        <Route path="/workspace" element={<Workspace />} />
        <Route path="/create" element={<Navigate to="/editor" replace />} />
        <Route path="/studio" element={<Studio />} />
        <Route path="/film" element={<Film />} />
        <Route path="/characters" element={<Characters />} />
        <Route path="/editor" element={<Editor />} />
        <Route path="/editor/:jobId" element={<Editor />} />
        <Route path="/storyboard" element={<Navigate to="/editor" replace />} />
        <Route path="/edit/:jobId" element={<Editor />} />
        <Route path="/videos" element={<MyVideos />} />
        <Route path="/series" element={<SeriesPage />} />
        <Route path="/profile" element={<Profile />} />
        <Route path="/account" element={<Navigate to="/profile" replace />} />
        <Route path="/user" element={<Navigate to="/profile" replace />} />
        <Route path="/job/:jobId" element={<JobStatus />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </ErrorBoundary>
  );
}