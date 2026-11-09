import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api, ApiError } from "../services/api";
import { Button, Spinner } from "../components/ui";
import { useAuth } from "../contexts/AuthContext";
import { useCloneVoices } from "../hooks/useCloneVoices";
import StepContent from "../components/create/StepContent";
import StepSettings from "../components/create/StepSettings";
import StepVisuals from "../components/create/StepVisuals";
import StepReview from "../components/create/StepReview";
import type { PreloadMedia, UploadedMedia, VideoSettings } from "../types";

const STEPS = ["Content", "Settings", "Visuals", "Review"];
const DRAFT_KEY = "vidyra.draft.v1";

const DEFAULT_SETTINGS: VideoSettings = {
  voice: "",
  speech_rate: 1.0,
  pacing: "Normal Sports Story",
  hook: true,
  hook_style: "Breaking News",
  hook_text: "",
  hook_subtext: "",
  hook_duration: 1.5,
  captions: import.meta.env.VITE_DEFAULT_CAPTIONS !== "off",
  caption_style: "Karaoke",
  font_size: 58,
  words_per_line: 5,
  caption_position: "Bottom",
  music: null,
  music_volume: 0.35,
  transition: "Auto",
  reframe: "auto",
  aspect: "auto",
  show_page_name: true,
  page_name: "",
};

interface DraftMedia {
  storagePath: string;
  name: string;
  type: "image" | "video";
  size: number;
  duration?: number;
}

interface Draft {
  title: string;
  script: string;
  media: DraftMedia[];
  cover: DraftMedia | null;
  settings: VideoSettings;
  step: number;
}

function stripUrls(m: UploadedMedia): DraftMedia {
  return { storagePath: m.storagePath, name: m.name, type: m.type, size: m.size, duration: m.duration };
}

export default function Create() {
  const navigate = useNavigate();
  const location = useLocation();
  const { pageName } = useAuth();
  const [step, setStep] = useState(0);
  const [hasDraft, setHasDraft] = useState(false);
  /**
   * Gates the autosave below. The draft is only *detected* on mount, not
   * loaded, so state still holds its empty initial values at that point -- and
   * an ungated debounced save would write those empty values straight over the
   * stored draft, destroying the script and every uploaded clip reference the
   * user was about to resume.
   */
  const autosaveReady = useRef(false);

  const [catalog, setCatalog] = useState<{
    voices: string[];
    music: string[];
    pacing: string[];
    hooks: string[];
    captionStyles: string[];
    captionPositions: string[];
    transitions: string[];
    minRate: number;
    maxRate: number;
  } | null>(null);

  const [script, setScript] = useState("");
  const [title, setTitle] = useState("");
  const [media, setMedia] = useState<UploadedMedia[]>([]);
  const [cover, setCover] = useState<UploadedMedia | null>(null);
  const [settings, setSettings] = useState<VideoSettings>(DEFAULT_SETTINGS);
  const cloneVoices = useCloneVoices();
  // Cloned-voice options ("clone:<path>") from Studio/library audio — same
  // voice dropdown, loaded once with the catalog.
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [loadError, setLoadError] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    // Detect an existing draft (without loading it automatically).
    try {
      setHasDraft(Boolean(localStorage.getItem(DRAFT_KEY)));
    } catch {
      /* ignore */
    }
    // Hold autosave until the draft banner has rendered: the user's choice to
    // resume or discard happens with these still-empty values on screen.
    const release = setTimeout(() => {
      autosaveReady.current = true;
    }, 0);
    return () => clearTimeout(release);
  }, []);

  // Assets handed over from AI Studio ("Use in video") land in navigation state.
  useEffect(() => {
    const state = location.state as { preload?: PreloadMedia | PreloadMedia[] } | null;
    const incoming = state?.preload
      ? Array.isArray(state.preload)
        ? state.preload
        : [state.preload]
      : [];
    if (incoming.length === 0) return;
    let alive = true;
    (async () => {
      const items = await Promise.all(
        incoming.map(async (m) => {
          let url = m.url || "";
          if (!url) {
            try {
              url = (await api.preview(m.path)).url;
            } catch {
              /* preview unavailable */
            }
          }
          return {
            id: m.path,
            name: m.name,
            type: m.kind,
            url,
            size: m.size ?? 0,
            storagePath: m.path,
            duration: m.duration,
          } as UploadedMedia;
        })
      );
      if (!alive) return;
      setMedia((prev) => [...items, ...prev]);
      navigate(location.pathname, { replace: true, state: null });
    })();
    return () => {
      alive = false;
    };
  }, [location.state, location.pathname, navigate]);

  useEffect(() => {
    let alive = true;

    async function loadCatalog() {
      setLoadError("");
      const settled = await Promise.allSettled([
        api.catalog(),
        api.catalogVoices(),
        api.catalogMusic(),
      ]);
      const presets = settled[0].status === "fulfilled" ? settled[0].value : null;
      const voices = settled[1].status === "fulfilled" ? settled[1].value : null;
      const music = settled[2].status === "fulfilled" ? settled[2].value : null;
      if (!alive) return;

      if (!presets && !voices && !music) {
        setLoadError("Could not reach the Vidyra server. Check that the backend is running and refresh to retry.");
        return;
      }

      setCatalog({
        voices: voices?.voices ?? [],
        music: music?.music ?? [],
        pacing: presets?.pacing ?? ["Breaking News", "Normal Sports Story", "High Energy", "Analysis"],
        hooks: presets?.hooks ?? ["Breaking News", "Question", "Bold Statement", "Debate", "Game Day"],
        captionStyles: presets?.caption_styles ?? ["Karaoke", "Clean", "Bold"],
        captionPositions: presets?.caption_positions ?? ["Top", "Center", "Bottom"],
        transitions: presets?.transitions ?? ["Auto", "fade", "slideleft", "slideright", "wipeleft", "wiperight"],
        minRate: presets?.speech_rate?.min ?? 0.85,
        maxRate: presets?.speech_rate?.max ?? 1.15,
      });
      if (voices?.voices?.length) {
        setSettings((s) => (s.voice ? s : { ...s, voice: voices.voices[0] }));
      }
    }

    loadCatalog();
    return () => {
      alive = false;
    };
  }, [reloadKey]);

  // Autosave draft (debounced).
  useEffect(() => {
    const pending = setTimeout(() => {
      if (!autosaveReady.current) return;
      try {
        localStorage.setItem(
          DRAFT_KEY,
          JSON.stringify({
            title,
            script,
            media: media.map(stripUrls),
            cover: cover ? stripUrls(cover) : null,
            settings,
            step,
          } satisfies Draft)
        );
      } catch {
        /* storage full / unavailable */
      }
    }, 500);
    return () => clearTimeout(pending);
  }, [title, script, media, cover, settings, step]);

  async function resumeDraft() {
    try {
      const raw = localStorage.getItem(DRAFT_KEY);
      if (!raw) return;
      const draft = JSON.parse(raw) as Draft;
      setTitle(draft.title || "");
      setScript(draft.script || "");
      setSettings({ ...DEFAULT_SETTINGS, ...draft.settings });
      setStep(typeof draft.step === "number" ? draft.step : 0);

      const hydrate = async (m: DraftMedia): Promise<UploadedMedia> => {
        let url = "";
        try {
          const res = await api.preview(m.storagePath);
          url = res.url;
        } catch {
          /* image/video preview unavailable */
        }
        return { id: m.storagePath, ...m, url };
      };

      const restored = await Promise.all((draft.media || []).map(hydrate));
      setMedia(restored);
      setCover(draft.cover ? await hydrate(draft.cover) : null);
      setHasDraft(false);
      clearDraft();
    } catch {
      clearDraft();
      setHasDraft(false);
    }
  }

  function clearDraft() {
    try {
      localStorage.removeItem(DRAFT_KEY);
    } catch {
      /* ignore */
    }
    setHasDraft(false);
  }

  function patch(p: Partial<VideoSettings>) {
    setSettings((s) => ({ ...s, ...p }));
  }

  const canContinue = useMemo(() => {
    if (step === 0) return script.trim().length > 0 && media.length > 0;
    if (step === 1) return settings.voice !== "" && settings.speech_rate >= (catalog?.minRate ?? 0.85);
    return true;
  }, [step, script, media, settings, catalog]);

  async function submit() {
    setSubmitting(true);
    setError("");
    try {
      const mediaInput = media.map((m) =>
        m.duration ? { path: m.storagePath, duration: m.duration } : { path: m.storagePath }
      );
      const res = await api.createJob({
        title: title.trim() || "My sports video",
        script,
        media: mediaInput,
        settings: { ...settings },
        cover: cover?.storagePath ?? null,
      });
      clearDraft();
      navigate(`/job/${res.job_id}`);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : (e as Error).message;
      setError(msg || "Could not create the job.");
      setSubmitting(false);
    }
  }

  if (!catalog) {
    if (loadError) {
      return (
        <div style={{ display: "grid", placeItems: "center", paddingTop: 90, gap: 12 }}>
          <p className="faint" style={{ marginTop: 12, maxWidth: 420, textAlign: "center" }}>
            {loadError}
          </p>
          <Button onClick={() => setReloadKey((k) => k + 1)}>Retry</Button>
        </div>
      );
    }
    return (
      <div style={{ display: "grid", placeItems: "center", paddingTop: 90 }}>
        <Spinner />
        <p className="faint" style={{ marginTop: 12 }}>Loading creative tools…</p>
      </div>
    );
  }

  return (
    <div className="fade-up">
      <h2 style={{ marginTop: 0 }}>Create Video</h2>

      {hasDraft && (
        <div className="card draft-banner" style={{ marginBottom: 14, borderColor: "rgba(139,92,246,0.4)" }}>
          <div>
            <div style={{ fontWeight: 800, fontSize: "0.95rem" }}>Unfinished video detected</div>
            <p className="faint" style={{ margin: "2px 0 0", fontSize: "0.8rem" }}>
              Pick up where you left off.
            </p>
          </div>
          <div className="draft-actions">
            <Button variant="ghost" onClick={clearDraft}>Discard</Button>
            <Button variant="primary" onClick={resumeDraft}>Resume</Button>
          </div>
        </div>
      )}

      <div className="segmented" style={{ marginBottom: 16 }}>
        {STEPS.map((s, i) => (
          <button
            key={s}
            type="button"
            className={`seg-btn ${step === i ? "active" : ""}`}
            style={{ cursor: i < step ? "pointer" : "default" }}
            onClick={() => i < step && setStep(i)}
          >
            {i + 1}. {s}
          </button>
        ))}
      </div>

      {error && (
        <div
          className="card"
          style={{ borderColor: "rgba(248,113,113,0.4)", marginBottom: 14, color: "var(--danger)", fontSize: "0.9rem" }}
        >
          {error}
        </div>
      )}

      {step === 0 && (
        <StepContent
          title={title}
          onTitle={setTitle}
          script={script}
          onScript={setScript}
          media={media}
          onMedia={setMedia}
          cover={cover}
          onCover={setCover}
        />
      )}
      {step === 1 && (
        <StepSettings
          settings={settings}
          onUpdate={patch}
          voices={catalog.voices}
          pacing={catalog.pacing}
          hooks={catalog.hooks}
          minRate={catalog.minRate}
          maxRate={catalog.maxRate}
          cloneVoices={cloneVoices}
        />
      )}
      {step === 2 && (
        <StepVisuals
          settings={settings}
          onUpdate={patch}
          captionStyles={catalog.captionStyles}
          captionPositions={catalog.captionPositions}
          transitions={catalog.transitions}
          music={catalog.music}
          pageName={pageName}
        />
      )}
      {step === 3 && (
        <StepReview
          script={script}
          media={media}
          settings={settings}
          submitting={submitting}
          onSubmit={submit}
          onBack={() => setStep(2)}
          voices={catalog.voices}
          pageName={pageName}
        />
      )}

      {step < 3 && (
        <div style={{ marginTop: 22 }}>
          <Button
            variant="primary"
            size="lg"
            disabled={!canContinue}
            onClick={() => setStep((s) => s + 1)}
          >
            {step === 0 ? "Next: Settings" : step === 1 ? "Next: Visuals" : "Next: Review"}
          </Button>
        </div>
      )}

      {step === 3 && (
        <div className="faint" style={{ textAlign: "center", marginTop: 18, fontSize: "0.82rem" }}>
          Your uploads are private to your account.
        </div>
      )}
    </div>
  );
}