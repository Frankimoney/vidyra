import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import { Card } from "../components/ui";
import DirectorRoom from "../components/planner/DirectorRoom";
import { DEFAULT_MUSIC_VOLUME } from "../components/create/MusicVolume";
import { useCloneVoices } from "../hooks/useCloneVoices";
import Studio from "./Studio";

type Mode = "film" | "image";

export default function StudioCreate() {
  const [mode, setMode] = useState<Mode>("film");
  const [aspect, setAspect] = useState("auto");
  const [captions, setCaptions] = useState(true);
  const [research] = useState(false);
  const [music] = useState("");
  const [musicVolume] = useState(DEFAULT_MUSIC_VOLUME);
  const [voice, setVoice] = useState("");
  const [voices, setVoices] = useState<string[]>([]);
  const [studioEnabled, setStudioEnabled] = useState<boolean | null>(null);
  const cloneVoices = useCloneVoices();

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([
      api.studioCatalog(),
      api.catalogVoices(),
      api.catalogMusic(),
    ]).then(([studio, voiceCatalog]) => {
      if (cancelled) return;
      if (studio.status === "fulfilled") setStudioEnabled(studio.value.enabled);
      if (voiceCatalog.status === "fulfilled") setVoices(voiceCatalog.value.voices);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Card>
        <div className="segmented" role="tablist" aria-label="Create mode">
          <button
            type="button"
            role="tab"
            aria-selected={mode === "film"}
            className={`seg-btn ${mode === "film" ? "active" : ""}`}
            onClick={() => setMode("film")}
          >
            Idea to film
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === "image"}
            className={`seg-btn ${mode === "image" ? "active" : ""}`}
            onClick={() => setMode("image")}
          >
            Photo to video
          </button>
        </div>
        <p className="faint" style={{ fontSize: "0.82rem", margin: "10px 0 0" }}>
          {mode === "film"
            ? "Describe the film once. The Director asks what it needs, then gives you an editable plan before any GPU is used."
            : "Upload a photo, describe the motion, and turn it into a short clip."}
        </p>
      </Card>

      {mode === "film" ? (
        <DirectorRoom
          aspect={aspect}
          onAspectChange={setAspect}
          captions={captions}
          onCaptionsChange={setCaptions}
          research={research}
          music={music}
          musicVolume={musicVolume}
          voice={voice}
          onVoiceChange={setVoice}
          voices={voices}
          cloneVoices={cloneVoices}
        />
      ) : studioEnabled === false ? (
        <Card>
          <p style={{ margin: 0 }}>
            Photo to video needs the Studio GPU endpoint. Configure RunPod on the
            backend, or use the Tools tab once it is online.
          </p>
        </Card>
      ) : (
        <>
          <Studio initialTool="img2video" embedded />
          <p className="faint" style={{ fontSize: "0.78rem", margin: 0 }}>
            Need a talking avatar or a restyle?{" "}
            <Link to="../tools" style={{ color: "var(--accent)" }}>
              Open Tools
            </Link>
            .
          </p>
        </>
      )}
    </div>
  );
}
