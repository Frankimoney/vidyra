import { Button, Card } from "../ui";
import type { VideoSettings, UploadedMedia } from "../../types";

interface Props {
  script: string;
  media: UploadedMedia[];
  settings: VideoSettings;
  submitting: boolean;
  onSubmit: () => void;
  onBack: () => void;
  voices: string[];
  pageName: string;
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="spread" style={{ padding: "7px 0" }}>
      <span className="muted" style={{ fontSize: "0.9rem" }}>{k}</span>
      <span style={{ fontWeight: 600, fontSize: "0.9rem", textAlign: "right", maxWidth: "60%" }}>
        {v}
      </span>
    </div>
  );
}

export default function StepReview({
  script,
  media,
  settings,
  submitting,
  onSubmit,
  onBack,
  voices,
  pageName,
}: Props) {
  const words = script.trim() ? script.trim().split(/\s+/).length : 0;
  const voice = voices.find((v) => v === settings.voice) || settings.voice;
  const showPage = settings.show_page_name ?? true;
  const effectivePage = (settings.page_name || pageName || "").trim();

  return (
    <div className="stack-v fade-up">
      <Card>
        <div className="thumb" style={{ width: "58%", margin: "0 auto 16px" }}>
          {media.length > 0 ? (
            media[0].type === "image" ? (
              <img src={media[0].url} alt="Preview" />
            ) : (
              <video src={media[0].url} muted playsInline preload="metadata" />
            )
          ) : (
            <div style={{ height: "100%", display: "grid", placeItems: "center", color: "var(--text-3)" }}>
              No media
            </div>
          )}
        </div>

        <div className="divider" />
        <Row k="Script" v={`${words} words`} />
        <Row k="Voice" v={voice} />
        <Row k="Speed" v={`${settings.speech_rate.toFixed(2)}x`} />
        <Row k="Pacing" v={settings.pacing} />
        <Row k="Hook" v={settings.hook ? `${settings.hook_style}` : "Off"} />
        <Row k="Captions" v={settings.captions ? `${settings.caption_style} · ${settings.font_size}px` : "Off"} />
        <Row
          k="Music"
          v={
            settings.music
              ? `${settings.music} · ${Math.round((settings.music_volume ?? 0.35) * 100)}%`
              : "None"
          }
        />
        <Row k="Transitions" v={settings.transition} />
        <Row k="Reframe" v={{ auto: "Auto", crop: "Full crop", blur: "Fit + blur" }[settings.reframe ?? "auto"] ?? "Auto"} />
        <Row k="Media" v={`${media.length} file${media.length === 1 ? "" : "s"}`} />
        <Row k="Page name" v={showPage ? (effectivePage ? `@${effectivePage}` : "Off (not set)") : "Off"} />
      </Card>

      <div className="btn-row">
        <Button variant="ghost" onClick={onBack}>Back</Button>
        <Button variant="primary" onClick={onSubmit} loading={submitting} disabled={submitting}>
          {submitting ? "Starting…" : "Generate Video"}
        </Button>
      </div>
    </div>
  );
}