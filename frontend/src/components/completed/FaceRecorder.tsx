import { useEffect, useRef, useState } from "react";
import { Button, Segmented, Spinner, Toggle } from "../ui";
import { api } from "../../services/api";
import { useToast } from "../feedback";
import type { FaceShape } from "../../types";

type Mode =
  | "start"
  | "setup"
  | "counting"
  | "recording"
  | "preview"
  | "uploading"
  | "compositing"
  | "error";

type Corner = "TL" | "TR" | "BL" | "BR";

const CORNERS: Corner[] = ["TL", "TR", "BL", "BR"];

/** Seconds counted down before the camera starts rolling. */
const COUNTDOWN_FROM = 3;

function pickMime(): string {
  if (typeof window === "undefined" || !window.MediaRecorder) return "";
  const head = "video/webm";
  const candidates = [
    `${head};codecs=vp9,opus`,
    `${head};codecs=vp8,opus`,
    `${head};codecs=vp8`,
    head,
  ];
  return candidates.find((c) => window.MediaRecorder.isTypeSupported(c)) || "";
}

function clock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function FaceRecorder({
  jobId,
  mainSrc,
  poster,
  onDone,
  onCancel,
}: {
  jobId: string;
  mainSrc?: string;
  poster?: string;
  onDone: (newJobId: string) => void;
  onCancel: () => void;
}) {
  const toast = useToast();

  const [mode, setMode] = useState<Mode>(() =>
    typeof window !== "undefined" && window.MediaRecorder ? "start" : "error"
  );
  const [camError, setCamError] = useState("");
  const [videoSize, setVideoSize] = useState({ w: 0, h: 0 });
  const [shape, setShape] = useState<FaceShape>("circle");
  const [scale, setScale] = useState(18);
  const [includeAudio, setIncludeAudio] = useState(false);
  const [pos, setPos] = useState({ x: 74, y: 2 });
  const [elapsed, setElapsed] = useState(0);
  const [countdown, setCountdown] = useState(COUNTDOWN_FROM);
  const [videoDuration, setVideoDuration] = useState(0);
  const [recording, setRecording] = useState<Blob | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);

  const camRef = useRef<HTMLVideoElement | null>(null);
  const mainRef = useRef<HTMLVideoElement | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  // Read by the main video's "ended" handler, which must not close over a
  // stale mode.
  const modeRef = useRef<Mode>(mode);
  modeRef.current = mode;
  // Set when the take was ended by the video finishing rather than by the user.
  const autoStoppedRef = useRef(false);
  const dragRef = useRef<{
    startX: number;
    startY: number;
    leftPx: number;
    topPx: number;
    faceWpx: number;
    faceHpx: number;
  } | null>(null);

  const aspect = videoSize.h > 0 ? videoSize.w / videoSize.h : 0.75;

  const showCanvas =
    mode === "setup" ||
    mode === "counting" ||
    mode === "recording" ||
    mode === "preview" ||
    mode === "uploading";
  // While taking, the main video must run once through so it can end the take.
  const mainLoops = mode === "setup" || mode === "preview" || mode === "uploading";
  // A known, finite length is what lets the take be cut to match the video.
  const timed = videoDuration > 0;
  const remaining = timed ? Math.max(0, videoDuration - elapsed) : 0;

  /** Face width/height as % of the canvas (scale is % of canvas HEIGHT). */
  function faceSizePct(): { w: number; h: number } {
    return { w: scale * aspect, h: scale };
  }

  function onStreamEnded() {
    const live = modeRef.current;
    if ((live === "recording" || live === "counting") && recorderRef.current && recorderRef.current.state !== "inactive") {
      recorderRef.current.stop();
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    setMode("start");
  }

  function stopCamera() {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  }

  async function startCamera(withAudio: boolean) {
    setMode("setup");
    setCamError("");
    stopCamera();
    try {
      const media = navigator.mediaDevices;
      if (!media?.getUserMedia) throw new Error("no-camera");
      const stream = await media.getUserMedia({
        video: { width: { ideal: 720 }, height: { ideal: 960 } },
        audio: withAudio,
      });
      streamRef.current = stream;
      stream.getVideoTracks()[0].addEventListener("ended", onStreamEnded);
      if (camRef.current) {
        camRef.current.srcObject = stream;
        await camRef.current.play().catch(() => undefined);
      }
      const settings = stream.getVideoTracks()[0].getSettings();
      if (settings.width && settings.height) {
        setVideoSize({ w: settings.width, h: settings.height });
      }
    } catch {
      setMode("error");
      setCamError(
        "Camera access was denied or unavailable. Allow camera access and try again."
      );
      return;
    }
    resetCorner("TR");
  }

  function resetCorner(corner: Corner) {
    const { w, h } = faceSizePct();
    const gap = 2;
    switch (corner) {
      case "TL":
        setPos({ x: gap, y: gap });
        break;
      case "TR":
        setPos({ x: Math.max(gap, 100 - w - gap), y: gap });
        break;
      case "BL":
        setPos({ x: gap, y: Math.max(gap, 100 - h - gap) });
        break;
      case "BR":
        setPos({ x: Math.max(gap, 100 - w - gap), y: Math.max(gap, 100 - h - gap) });
        break;
    }
  }

  /** Rewind the main video to the top and play it, so the take starts in sync. */
  function playMainFromStart() {
    const v = mainRef.current;
    if (!v) return;
    try {
      v.currentTime = 0;
    } catch {
      /* seeking before metadata is ready — play() will start from the top */
    }
    v.play().catch(() => undefined);
  }

  function beginCountdown() {
    setCountdown(COUNTDOWN_FROM);
    setElapsed(0);
    playMainFromStart();
    setMode("counting");
  }

  function cancelCountdown() {
    setMode("setup");
  }

  /** Leave the take and go back to the idle canvas, with the video looping. */
  function backToSetup() {
    setMode("setup");
    const v = mainRef.current;
    if (v) {
      v.loop = true;
      playMainFromStart();
    }
  }

  function startRecording() {
    const stream = streamRef.current;
    if (!stream || typeof window.MediaRecorder === "undefined") return;
    chunksRef.current = [];
    autoStoppedRef.current = false;
    const rec = new MediaRecorder(stream, {
      mimeType: pickMime() || undefined,
      videoBitsPerSecond: 2_500_000,
    });
    recorderRef.current = rec;
    rec.ondataavailable = (e) => {
      if (e.data.size > 0) chunksRef.current.push(e.data);
    };
    rec.onstop = () => {
      const autoStopped = autoStoppedRef.current;
      autoStoppedRef.current = false;
      const blob = chunksRef.current.length
        ? new Blob(chunksRef.current, { type: rec.mimeType || "video/webm" })
        : null;
      if (blob) {
        setRecording(blob);
        setPreviewUrl((old) => {
          if (old) URL.revokeObjectURL(old);
          return URL.createObjectURL(blob);
        });
        setElapsed(0);
        setMode("preview");
        if (autoStopped) {
          toast("Recording stopped at the end of the video.", "success");
        }
      } else {
        setMode("setup");
      }
    };
    rec.start();
    setElapsed(0);
    setMode("recording");
    // The take runs exactly as long as the video it sits on.
    playMainFromStart();
  }

  function stopRecording(auto = false) {
    autoStoppedRef.current = auto;
    if (recorderRef.current && recorderRef.current.state !== "inactive") {
      recorderRef.current.stop();
    }
  }

  useEffect(() => {
    const t = setInterval(() => {
      if (mode === "recording") setElapsed((s) => s + 1);
    }, 1000);
    return () => clearInterval(t);
  }, [mode]);

  // Countdown 3 → 2 → 1 → roll.
  useEffect(() => {
    if (mode !== "counting") return;
    if (countdown <= 0) {
      startRecording();
      return;
    }
    const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, countdown]);

  // Learn the main video's length so the take can be cut to match it.
  useEffect(() => {
    const v = mainRef.current;
    if (!v) return;
    const read = () => {
      if (Number.isFinite(v.duration) && v.duration > 0) {
        setVideoDuration(v.duration);
      }
    };
    read();
    v.addEventListener("loadedmetadata", read);
    v.addEventListener("durationchange", read);
    return () => {
      v.removeEventListener("loadedmetadata", read);
      v.removeEventListener("durationchange", read);
    };
  }, [mainSrc, showCanvas]);

  // The video finishing ends the take.
  useEffect(() => {
    const v = mainRef.current;
    if (!v) return;
    const onEnded = () => {
      if (modeRef.current === "recording") stopRecording(true);
    };
    v.addEventListener("ended", onEnded);
    return () => v.removeEventListener("ended", onEnded);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showCanvas]);

  useEffect(() => {
    return () => {
      stopCamera();
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = () => canvas.getBoundingClientRect();

    function onPointerDown(e: PointerEvent) {
      const r = rect();
      const { w, h } = faceSizePct();
      dragRef.current = {
        startX: e.clientX,
        startY: e.clientY,
        leftPx: (pos.x / 100) * r.width,
        topPx: (pos.y / 100) * r.height,
        faceWpx: (w / 100) * r.width,
        faceHpx: (h / 100) * r.height,
      };
      (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    }

    function onPointerMove(e: PointerEvent) {
      const d = dragRef.current;
      if (!d) return;
      const r = rect();
      const nxPx = Math.max(0, Math.min(d.leftPx + (e.clientX - d.startX), r.width - d.faceWpx));
      const nyPx = Math.max(0, Math.min(d.topPx + (e.clientY - d.startY), r.height - d.faceHpx));
      setPos({ x: (nxPx / Math.max(1, r.width)) * 100, y: (nyPx / Math.max(1, r.height)) * 100 });
    }

    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", () => (dragRef.current = null));
    return () => {
      canvas.removeEventListener("pointerdown", onPointerDown);
      canvas.removeEventListener("pointermove", onPointerMove);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pos.x, pos.y, aspect, scale]);

  const { w: faceW } = faceSizePct();

  async function composite() {
    if (!recording) return;
    setMode("uploading");
    try {
      const up = await api.uploadFaceRecording(recording, `face-${Date.now()}.webm`);
      toast("Recording uploaded. Compositing…", "success");
      setMode("compositing");
      const res = await api.addFaceRecording(jobId, {
        recordingPath: up.storagePath,
        duration: up.duration,
        overlay: {
          shape,
          scale,
          x: Math.round(pos.x * 10) / 10,
          y: Math.round(pos.y * 10) / 10,
          recordingWidth: videoSize.w,
          recordingHeight: videoSize.h,
        },
        includeAudio,
      });
      onDone(res.jobId);
    } catch (e) {
      setMode("preview");
      toast((e as Error).message || "Could not create the face video.", "error");
    }
  }

  return (
    <div className="card" style={{ textAlign: "left" }}>
      <div className="spread" style={{ marginBottom: 10 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: "1.05rem" }}>Add Your Face</h3>
          <p className="muted" style={{ margin: "2px 0 0", fontSize: "0.85rem" }}>
            Record a short intro. It appears over your video.
          </p>
        </div>
        <Button variant="ghost" onClick={onCancel} style={{ padding: "6px 12px" }}>
          ✕
        </Button>
      </div>

      {mode === "start" && (
        <div style={{ textAlign: "center", padding: "14px 0" }}>
          <p className="muted" style={{ fontSize: "0.9rem", maxWidth: 420, margin: "0 auto 14px" }}>
            Uses your webcam in the browser. Nothing is uploaded until you finish recording.
          </p>
          <Button variant="primary" onClick={() => startCamera(includeAudio)}>
            Start Recording
          </Button>
        </div>
      )}

      {mode === "error" && (
        <div style={{ textAlign: "center", padding: "14px 0" }}>
          <p style={{ color: "var(--danger)", fontSize: "0.9rem" }}>
            {camError ||
              "This browser doesn't support recording. Try a recent Chrome, Edge, or Firefox."}
          </p>
          <div className="btn-row" style={{ justifyContent: "center" }}>
            {camError && (
              <Button variant="primary" onClick={() => startCamera(includeAudio)}>
                Retry
              </Button>
            )}
            <Button variant="ghost" onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      {showCanvas && (
        <>
          <div
            ref={canvasRef}
            className="face-canvas"
            style={{
              position: "relative",
              width: "100%",
              aspectRatio: "9 / 16",
              background: "#000",
              borderRadius: 12,
              overflow: "hidden",
              userSelect: "none",
              touchAction: "none",
            }}
          >
            {mainSrc && (
              <video
                ref={mainRef}
                src={mainSrc}
                poster={poster}
                muted
                loop={mainLoops}
                playsInline
                autoPlay
                style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }}
              />
            )}
            {(mode === "setup" || mode === "counting" || mode === "recording") && (
              <video
                ref={camRef}
                muted
                playsInline
                style={{
                  position: "absolute",
                  left: `${pos.x}%`,
                  top: `${pos.y}%`,
                  width: `${faceW}%`,
                  aspectRatio: `${videoSize.w || 4}/${videoSize.h || 3}`,
                  borderRadius: shape === "circle" ? "50%" : shape === "rounded" ? "14%" : 0,
                  border: "2px solid rgba(255,255,255,0.6)",
                  objectFit: "cover",
                  cursor: "grab",
                  boxShadow: "0 2px 14px rgba(0,0,0,0.35)",
                }}
              />
            )}
            {mode === "preview" && previewUrl && (
              <video
                src={previewUrl}
                muted
                controls
                playsInline
                loop
                style={{
                  position: "absolute",
                  left: `${pos.x}%`,
                  top: `${pos.y}%`,
                  width: `${faceW}%`,
                  aspectRatio: `${videoSize.w || 4}/${videoSize.h || 3}`,
                  borderRadius: shape === "circle" ? "50%" : shape === "rounded" ? "14%" : 0,
                  border: "2px solid rgba(255,255,255,0.6)",
                  objectFit: "cover",
                  boxShadow: "0 2px 14px rgba(0,0,0,0.35)",
                }}
              />
            )}
            {mode === "recording" && (
              <div
                style={{
                  position: "absolute",
                  top: 10,
                  left: 0,
                  right: 0,
                  textAlign: "center",
                  color: "#fff",
                  fontSize: "0.85rem",
                  fontWeight: 700,
                  textShadow: "0 1px 4px rgba(0,0,0,0.8)",
                  display: "flex",
                  justifyContent: "center",
                  alignItems: "center",
                  gap: 8,
                }}
              >
                <span style={{ width: 10, height: 10, borderRadius: "50%", background: "#f87171" }} />
                REC {clock(elapsed)}
                {timed && (
                  <span style={{ opacity: 0.8, fontWeight: 600 }}>
                    / {clock(videoDuration)} · {clock(remaining)} left
                  </span>
                )}
              </div>
            )}

            {mode === "counting" && (
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  background: "rgba(0,0,0,0.45)",
                  pointerEvents: "none",
                }}
              >
                <span
                  key={countdown}
                  style={{
                    fontSize: "5rem",
                    fontWeight: 800,
                    color: "#fff",
                    textShadow: "0 4px 18px rgba(0,0,0,0.85)",
                    animation: "none",
                  }}
                >
                  {countdown > 0 ? countdown : "GO"}
                </span>
              </div>
            )}
          </div>

          <div className="field" style={{ marginTop: 14 }}>
            <label className="field-label">Overlay shape</label>
            <Segmented
              options={["circle", "rounded", "rect"] as const}
              value={shape}
              onChange={setShape}
            />
          </div>

          <div className="field">
            <div className="spread">
              <label className="field-label">Size</label>
              <span className="faint" style={{ fontSize: "0.8rem" }}>
                {scale}%
              </span>
            </div>
            <input
              type="range"
              min={10}
              max={40}
              step={1}
              value={scale}
              onChange={(e) => setScale(Number(e.target.value))}
              style={{ width: "100%" }}
            />
          </div>

          <div className="field">
            <label className="field-label">Position</label>
            <div className="stack-h" style={{ gap: 8, flexWrap: "wrap" }}>
              {CORNERS.map((c) => (
                <button key={c} type="button" className="seg-btn" onClick={() => resetCorner(c)}>
                  {c}
                </button>
              ))}
              <span className="faint" style={{ fontSize: "0.75rem", alignSelf: "center" }}>
                or drag the overlay
              </span>
            </div>
          </div>

          <div className="field">
            <Toggle
              on={includeAudio}
              onChange={(v) => {
                setIncludeAudio(v);
                if (mode === "setup") startCamera(v);
              }}
              label="Include my microphone audio"
            />
          </div>

            {mode === "setup" && timed && (
              <p
                className="faint"
                style={{ margin: "0 0 10px", fontSize: "0.8rem", textAlign: "center" }}
              >
                Recording stops automatically when the video ends ({clock(videoDuration)}).
              </p>
            )}

            <div className="btn-row" style={{ marginTop: 14 }}>
            {mode === "setup" && (
              <Button variant="primary" onClick={beginCountdown} style={{ flex: 1 }}>
                ● Record
              </Button>
            )}
            {mode === "counting" && (
              <Button variant="ghost" onClick={cancelCountdown} style={{ flex: 1 }}>
                Cancel
              </Button>
            )}
            {mode === "recording" && (
              <Button variant="primary" onClick={() => stopRecording(false)} style={{ flex: 1 }}>
                ■ Stop
              </Button>
            )}
            {mode === "preview" && (
              <>
                <Button variant="ghost" onClick={backToSetup} style={{ flex: 1 }}>
                  ↻ Record again
                </Button>
                <Button variant="primary" onClick={composite} style={{ flex: 1 }}>
                  ✓ Use this clip
                </Button>
              </>
            )}
            {mode === "uploading" && (
              <Button variant="primary" loading style={{ flex: 1 }}>
                Uploading…
              </Button>
            )}
          </div>
        </>
      )}

      {mode === "compositing" && (
        <div style={{ textAlign: "center", padding: "18px 0" }}>
          <Spinner />
          <p style={{ margin: "12px 0 0", fontSize: "0.95rem" }}>
            Compositing your face recording…
            <br />
            <span className="muted" style={{ fontSize: "0.85rem" }}>
              This runs as a background job. It takes a few seconds.
            </span>
          </p>
        </div>
      )}
    </div>
  );
}