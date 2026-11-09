import { useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../services/api";
import type { PackagePreview } from "../services/api";
import { useToast } from "../components/feedback";
import { Button, Card, Field, Spinner, Toggle } from "../components/ui";
import VoiceSelect from "../components/VoiceSelect";
import AspectSelect from "../components/AspectSelect";
import DirectorRoom from "../components/planner/DirectorRoom";
import { StudioQuotaMeter } from "../components/StudioQuotaMeter";
import MusicVolume, {
  DEFAULT_MUSIC_VOLUME,
} from "../components/create/MusicVolume";
import { useCloneVoices } from "../hooks/useCloneVoices";

const STYLES = [
  { id: "cinematic", label: "🎬 Cinematic", hint: "Strong light, big emotions" },
  { id: "anime", label: "✨ Anime", hint: "Bold colors, expressive faces" },
  { id: "comedy", label: "😄 Comedy", hint: "Fast, funny, chaotic" },
  { id: "horror", label: "🌙 Horror", hint: "Shadows and suspense" },
  { id: "sci-fi", label: "🛰 Sci-Fi", hint: "Futuristic worlds" },
  { id: "fantasy", label: "🏰 Fantasy", hint: "Magic and adventure" },
  { id: "sports", label: "⚽ Sports", hint: "Underdog stories" },
  { id: "documentary", label: "🎥 Documentary", hint: "Real stories, calm tone" },
];

interface CharDraft {
  id: string;
  name: string;
  description: string;
  /** Piper name or "clone:<path>". Empty → Vidyra assigns one. */
  voice: string;
}

let _seq = 0;
const nextId = () => `c${Date.now().toString(36)}${(_seq++).toString(36)}`;

export default function Film() {
  const toast = useToast();
  const navigate = useNavigate();
  const [search] = useSearchParams();

  // The front door on Home hands people straight to the right workspace, so
  // they never have to pick a mode before they can start.
  const linkedProject = search.get("project") || "";
  const linkedMode = search.get("mode");
  const linkedIdea = search.get("idea") || "";

  const [mode, setMode] = useState<"film" | "podcast" | "director">(
    linkedMode === "director" || linkedMode === "podcast" || linkedMode === "film"
      ? linkedMode
      : "film"
  );
  const [idea, setIdea] = useState(linkedIdea);
  const [style, setStyle] = useState("cinematic");
  const [scenesCount, setScenesCount] = useState(8);
  const [turnsCount, setTurnsCount] = useState(8);
  // Solo default: lip-sync OFF (biggest GPU cost + failure point — enable per hero video).
  const [lipSync, setLipSync] = useState(false);
  const [captions, setCaptions] = useState(true);
  const [music, setMusic] = useState<string[]>([]);
  const [chosenMusic, setChosenMusic] = useState("");
  const [musicVolume, setMusicVolume] = useState(DEFAULT_MUSIC_VOLUME);
  const [characters, setCharacters] = useState<CharDraft[]>([]);
  const [voices, setVoices] = useState<string[]>([]);
  const [narratorVoice, setNarratorVoice] = useState("");
  const cloneVoices = useCloneVoices();
  const [research, setResearch] = useState(true);
  const [aspect, setAspect] = useState("auto");
  const [preview, setPreview] = useState<PackagePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [studioEnabled, setStudioEnabled] = useState<boolean | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.studioCatalog(), api.catalogMusic(), api.catalogVoices()])
      .then(([studio, m, v]) => {
        if (cancelled) return;
        setMusic(m.music);
        setVoices(v.voices);
        setStudioEnabled(studio.enabled);
      })
      .catch(() => {
        if (!cancelled) setStudioEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function addCharacter() {
    setCharacters((prev) => [
      ...prev,
      { id: nextId(), name: "", description: "", voice: "" },
    ]);
  }

  const trimmedIdea = idea.trim();
  const packageMode =
    mode === "film" && looksLikePackage(trimmedIdea);

  function looksLikePackage(t: string): boolean {
    if (!t) return false;
    if (t.startsWith("{") || t.startsWith("[")) return true;
    return (
      /\bscene\s+[0-9]/i.test(t) ||
      /\bscene\s*[:.]/i.test(t) ||
      /\bcamera\s+angle\s*[:.]/i.test(t) ||
      /^\s*\d+[.)]\s*(setting|scene)\b/i.test(t)
    );
  }

  async function runValidate() {
    if (trimmedIdea.length < 8) {
      toast("Paste your production package first.", "error");
      return;
    }
    setPreviewLoading(true);
    setPreview(null);
    setError("");
    try {
      setPreview(await api.validatePackage(trimmedIdea));
    } catch (e) {
      setError((e as Error).message || "Could not validate the package.");
    } finally {
      setPreviewLoading(false);
    }
  }

  async function copyTemplate() {
    const template = [
      "You are my AI film producer. Create a complete production package for a short vertical (9:16) video.",
      "Return ONLY a JSON object fenced in ```json. Do not add commentary.",
      "",
      "JSON shape:",
      '{',
      '  "title": "short engaging title",',
      '  "style": "cinematic | documentary | sports | anime | horror | comedy | sci-fi | fantasy",',
      '  "instructions": "director notes for tone, pacing, responsible storytelling",',
      '  "pacing": "Normal Sports Story",',
      '  "music": "pulse.wav" (optional built-in track),',
      '  "characters": [',
      '    { "name": "Narrator", "description": "appearance for an AI image generator", "voice": "optional piper voice" }',
      '  ],',
      '  "scenes": [',
      '    {',
      '      "narration": "voice-over text, 1-3 sentences",',
      '      "dialogue": { "CharacterName": "one spoken line" },',
      '      "setting": "rich visual prompt: location, objects, time of day, lighting — end with cinematic, 9:16",',
      '      "shot": "camera angle + motion, e.g. slow push-in from the baseline",',
      '      "transition": "fade | slideleft | slideright | wipeleft | wiperight | zoomin"',
      '    }',
      '  ]',
      '}',
      "",
      "Rules:",
      "- 2 to 16 scenes; every scene needs a `setting` and either narration or dialogue.",
      "- Only character names declared in `characters` may speak.",
      "- For real people or teams use verified, current facts — never invent statistics.",
      "- If I give you plain text instead of a topic, follow it exactly as written.",
      "",
      "MY REQUEST:",
      trimmedIdea,
    ].join("\n");
    try {
      await navigator.clipboard.writeText(template);
      toast("Template copied — paste it into ChatGPT with your request.", "success");
    } catch {
      toast("Couldn't copy. Select and copy the idea box manually.", "error");
    }
  }

  function onUpload(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => setIdea(String(r.result ?? ""));
    r.readAsText(f);
    e.target.value = "";
  }

  async function submit() {
    if (trimmedIdea.length < 8) {
      toast("Describe your idea (or paste a production package) in 1–2 sentences.", "error");
      return;
    }
    const cleanChars = characters
      .filter((c) => c.name.trim())
      .map((c) => ({
        name: c.name.trim(),
        description: c.description.trim(),
        voice: c.voice,
      }));
    setSubmitting(true);
    setError("");
    try {
      if (mode === "podcast") {
        const res = await api.createPodcast({
          topic: trimmedIdea,
          turns_count: turnsCount,
          hosts: cleanChars,
          captions,
          lip_sync: lipSync,
          music: chosenMusic,
          music_volume: musicVolume,
          research,
          voice: narratorVoice,
          aspect,
        });
        toast("Podcast is being written and recorded.", "success");
        navigate(`/job/${res.job_id}`);
        return;
      }
      const res = await api.createFilm({
        idea: packageMode ? "" : trimmedIdea,
        outline: packageMode ? trimmedIdea : "",
        style,
        scenes_count: scenesCount,
        characters: cleanChars,
        captions,
        music: chosenMusic,
        music_volume: musicVolume,
        research,
        voice: narratorVoice,
        aspect,
      });
      toast("Film is being written and filmed.", "success");
      navigate(`/job/${res.job_id}`);
    } catch (e) {
      const msg = (e as Error).message || "Failed to start your creation.";
      setError(msg);
      toast(msg, "error");
    } finally {
      setSubmitting(false);
    }
  }

  if (studioEnabled === false) {
    return (
      <div className="fade-up">
        <header style={{ marginBottom: 18 }}>
          <h2 style={{ marginTop: 0 }}>🎬 Auto Film</h2>
        </header>
        <Card>
          <p>
            Auto Film needs AI Studio to generate the shots. Configure RunPod GPU
            credentials (RUNPOD_API_KEY + RUNPOD_ENDPOINT_ID) on the backend to
            unlock it.
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="fade-up" style={{ paddingBottom: 24 }}>
      <header style={{ marginBottom: 18 }}>
        <h2 style={{ marginTop: 0 }}>
          {mode === "podcast" ? "🎙️ AI Podcast" : mode === "director" ? "🎬 Director" : "🎬 Auto Film"}
        </h2>
        <p className="faint" style={{ fontSize: "0.88rem", marginTop: -4 }}>
          {mode === "podcast"
            ? "Pick a topic and get a talking-head podcast video: Vidyra writes the episode, designs both hosts, records the conversation in natural voices, and edits it into a finished video."
            : mode === "director"
            ? "Plan the film with an agent before spending anything. It asks what it needs, drafts a production plan you can edit, and only films once you approve."
            : "Describe an idea in one line, or paste a full production package from ChatGPT (text or JSON with camera angles, scenes and dialogue) — Vidyra writes what's missing, films every scene with AI, adds voices, and edits it into a finished video."}
        </p>
      </header>

      <div style={{ display: "grid", gap: 12, marginBottom: 18 }}>
        <div
          className="spread"
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(3, 1fr)",
            gap: 8,
          }}
        >
          {(
            [
              { id: "film" as const, label: "🎬 Movie" },
              { id: "director" as const, label: "🎬 Director" },
              { id: "podcast" as const, label: "🎙️ Podcast" },
            ]
          ).map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => setMode(m.id)}
              className="input"
              style={{
                cursor: "pointer",
                textAlign: "center",
                fontWeight: 700,
                padding: "10px 0",
                borderColor:
                  mode === m.id
                    ? "var(--primary-strong, #6366f1)"
                    : "var(--line, rgba(255,255,255,0.12))",
                color: mode === m.id ? "var(--text, #fff)" : "var(--text-3)",
              }}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      <div style={{ display: "grid", gap: 18 }}>
        {mode === "director" ? (
          <DirectorRoom
            aspect={aspect}
            onAspectChange={setAspect}
            captions={captions}
            onCaptionsChange={setCaptions}
            research={research}
            music={chosenMusic}
            musicVolume={musicVolume}
            voice={narratorVoice}
            onVoiceChange={setNarratorVoice}
            voices={voices}
            cloneVoices={cloneVoices}
            initialProjectId={linkedProject}
          />
        ) : (
          <>
        <Card>
          <Field label={mode === "podcast" ? "Podcast topic" : "Film idea or production package"}>
            <textarea
              className="input"
              rows={packageMode ? 6 : 3}
              maxLength={mode === "podcast" ? 2000 : 40000}
              placeholder={
                mode === "podcast"
                  ? "e.g. Why everyone is suddenly talking about sleep — and what actually works"
                  : "e.g. A young inventor on a rainy island discovers a machine that can speak to birds — and must use it to save her town.\n\n…or paste a full production package (text or JSON) from ChatGPT with camera angles, scenes and dialogue."
              }
              value={idea}
              onChange={(e) => setIdea(e.target.value)}
            />
          </Field>
          {packageMode && (
            <div
              className="faint"
              style={{
                fontSize: "0.82rem",
                marginTop: 4,
                padding: "10px 12px",
                border: "1px dashed var(--line, rgba(255,255,255,0.18))",
                borderRadius: 12,
              }}
            >
              Production package detected — the agent will execute your scenes
              exactly as written. Check it with “Validate & preview” before you
              spend GPU runs.
            </div>
          )}

          {mode === "film" && !packageMode && (
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                gap: 8,
                marginTop: 8,
              }}
            >
              {STYLES.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setStyle(s.id)}
                  className="input"
                  style={{
                    textAlign: "left",
                    cursor: "pointer",
                    borderColor:
                      style === s.id
                        ? "var(--primary-strong, #6366f1)"
                        : "var(--line, rgba(255,255,255,0.12))",
                    color: style === s.id ? "var(--text, #fff)" : "var(--text-3)",
                  }}
                >
                  <div style={{ fontWeight: 700, fontSize: "0.92rem" }}>{s.label}</div>
                  <div className="faint" style={{ fontSize: "0.75rem" }}>
                    {s.hint}
                  </div>
                </button>
              ))}
            </div>
          )}

          {mode === "film" ? (
            packageMode ? (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                  gap: 8,
                  marginTop: 10,
                }}
              >
                <Button variant="ghost" onClick={copyTemplate}>
                  📋 Copy ChatGPT template
                </Button>
                <Button variant="ghost" onClick={() => fileRef.current?.click()}>
                  📁 Upload .json
                </Button>
                <Button onClick={runValidate} disabled={previewLoading}>
                  {previewLoading ? <Spinner /> : "🔎 Validate & preview"}
                </Button>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".json, application/json"
                  style={{ display: "none" }}
                  onChange={onUpload}
                />
              </div>
            ) : (
              <>
                <Field label={`Length: ${scenesCount} scenes`}>
                  <input
                    type="range"
                    min={2}
                    max={16}
                    step={1}
                    value={scenesCount}
                    onChange={(e) => setScenesCount(Number(e.target.value))}
                    style={{ width: "100%" }}
                  />
                </Field>
                <div className="faint" style={{ fontSize: "0.78rem", marginTop: 4 }}>
                  Each scene uses 2 AI Studio runs (a generated shot, then its motion).
                  Longer films use more of your daily quota.
                  <span style={{ marginLeft: 8 }}><StudioQuotaMeter /></span>
                </div>
              </>
            )
          ) : (
            <>
              <Field label={`Episode length: ${turnsCount} talking turns`}>
                <input
                  type="range"
                  min={2}
                  max={12}
                  step={1}
                  value={turnsCount}
                  onChange={(e) => setTurnsCount(Number(e.target.value))}
                  style={{ width: "100%" }}
                />
              </Field>
              <div className="faint" style={{ fontSize: "0.78rem", marginTop: 4 }}>
                Each turn uses 1 AI run, plus 1 portrait per host (2–4 runs total).
                Longer episodes use more of your daily quota.
                <span style={{ marginLeft: 8 }}><StudioQuotaMeter /></span>
              </div>
              <div style={{ marginTop: 10, display: "grid", gap: 6 }}>
                <Toggle
                  on={lipSync}
                  onChange={setLipSync}
                  label="Lip-sync: hosts' mouths move with the words"
                />
              </div>
            </>
          )}

          <div style={{ marginTop: 10, display: "grid", gap: 6 }}>
            <Toggle on={captions} onChange={setCaptions} label="Captions" />
          </div>
          <div style={{ marginTop: 10, display: "grid", gap: 6 }}>
            <Toggle
              on={research}
              onChange={setResearch}
              label={
                packageMode
                  ? "Fact-check the package online: verify names & stats via Gemini web search before filming (off only: film it as written, faster)"
                  : "Research online: verify real facts (needs a Gemini API key; falls back to offline writing)"
              }
            />
          </div>

          <Field label="Narrator voice (optional)">
            <VoiceSelect
              value={narratorVoice}
              onChange={setNarratorVoice}
              voices={voices}
              cloneVoices={cloneVoices}
              inheritLabel="Auto — Vidyra picks a voice"
            />
            <div className="faint" style={{ fontSize: "0.8rem", marginTop: 6 }}>
              Pick a cloned voice to narrate the whole film — 1 extra GPU run,
              reused for every scene.
            </div>
          </Field>

          <Field label="Music (optional)">
            <select
              className="input"
              value={chosenMusic}
              onChange={(e) => setChosenMusic(e.target.value)}
            >
              <option value="">None</option>
              {music.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Aspect ratio">
            <AspectSelect
              id="film-aspect"
              value={aspect}
              onChange={setAspect}
              hint="Sets the shape of every generated still and the final cut. Auto keeps Auto Film's vertical default."
            />
          </Field>

          {chosenMusic && (
            <MusicVolume value={musicVolume} onChange={setMusicVolume} />
          )}
        </Card>

        {packageMode ? (
          <Card>
            <div style={{ fontWeight: 700 }}>Characters</div>
            <div className="faint" style={{ fontSize: "0.85rem", marginTop: 4 }}>
              Your package defines them (name, look, optional voice) — nothing to
              set up here.
            </div>
          </Card>
        ) : (
        <Card>
          <div className="spread" style={{ marginBottom: 8 }}>
            <div>
              <div style={{ fontWeight: 700 }}>
                {mode === "podcast" ? "Hosts" : "Characters"}
              </div>
              <div className="faint" style={{ fontSize: "0.82rem" }}>
                {mode === "podcast"
                  ? "Optional. Add 1–2 hosts (or 3–4); Vidyra designs a consistent look and assigns each a unique voice."
                  : "Optional. Descriptions steer the AI shots; set a voice per character or let Vidyra assign one."}
              </div>
            </div>
            <Button onClick={addCharacter}>
              {mode === "podcast" ? "＋ Host" : "＋ Character"}
            </Button>
          </div>
          {characters.length === 0 ? (
            <div className="faint" style={{ fontSize: "0.85rem" }}>
              {mode === "podcast"
                ? "No hosts — Vidyra will create Alex and Sam."
                : "No characters — Vidyra will invent them."}
            </div>
          ) : (
            <div className="stack-v">
              {characters.map((c) => (
                <div key={c.id} style={{ display: "grid", gap: 8 }}>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 8 }}>
                    <input
                      className="input"
                      placeholder={mode === "podcast" ? "Host name (e.g. Alex)" : "Name (e.g. Ava)"}
                      value={c.name}
                      onChange={(e) =>
                        setCharacters((prev) =>
                          prev.map((x) =>
                            x.id === c.id ? { ...x, name: e.target.value } : x
                          )
                        )
                      }
                    />
                    <Button
                      variant="ghost"
                      onClick={() =>
                        setCharacters((prev) => prev.filter((x) => x.id !== c.id))
                      }
                    >
                      ✕
                    </Button>
                  </div>
                  <input
                    className="input"
                    placeholder={
                      mode === "podcast"
                        ? "Appearance & personality (e.g. a friendly tech journalist in a denim jacket)"
                        : "Appearance (e.g. a 12-year-old with a red raincoat)"
                    }
                    value={c.description}
                    onChange={(e) =>
                      setCharacters((prev) =>
                        prev.map((x) =>
                          x.id === c.id ? { ...x, description: e.target.value } : x
                        )
                      )
                    }
                  />
                  <VoiceSelect
                    value={c.voice}
                    onChange={(voice) =>
                      setCharacters((prev) =>
                        prev.map((x) => (x.id === c.id ? { ...x, voice } : x))
                      )
                    }
                    voices={voices}
                    cloneVoices={cloneVoices}
                    inheritLabel="Auto — Vidyra picks a voice"
                  />
                </div>
              ))}
            </div>
          )}
        </Card>
        )}

        {packageMode && !preview && (
          <Card>
            <div className="faint" style={{ fontSize: "0.85rem", margin: 0 }}>
              Press “Validate &amp; preview” (above) to see exactly what the agent
              will film before it spends any GPU runs.
            </div>
          </Card>
        )}

        {packageMode && preview && (
          <Card>
            <div className="spread" style={{ alignItems: "flex-start", marginBottom: 8 }}>
              <div>
                <div style={{ fontWeight: 700 }}>
                  {preview.valid ? "✅ Package ready to film" : "⚠️ Package needs fixes"}
                </div>
                <div className="faint" style={{ fontSize: "0.9rem" }}>
                  {preview.title} · {preview.charactersCount ?? 0} characters ·{" "}
                  {preview.scenesCount ?? 0} scenes · ~{preview.runsNeeded ?? 0} GPU runs
                </div>
              </div>
              <Button
                variant="ghost"
                onClick={() => {
                  setPreview(null);
                }}
              >
                ✕
              </Button>
            </div>
            {preview.issues.length > 0 && (
              <ul style={{ margin: "0 0 10px", paddingLeft: 18, color: "var(--danger, #f87171)" }}>
                {preview.issues.map((iss, i) => (
                  <li key={i} style={{ fontSize: "0.85rem" }}>
                    {iss}
                  </li>
                ))}
              </ul>
            )}
            <div
              style={{
                display: "grid",
                gap: 8,
                maxHeight: 260,
                overflowY: "auto",
                paddingRight: 4,
              }}
            >
              {(preview.outline?.scenes ?? []).map((sc, i) => (
                <div
                  key={i}
                  className="input"
                  style={{ pointerEvents: "none", padding: 8, fontSize: "0.82rem" }}
                >
                  <b>Scene {i + 1}</b>
                  {sc.shot && <span className="faint"> · 🎥 {sc.shot}</span>}
                  {sc.setting && (
                    <div className="faint" style={{ marginTop: 3 }}>
                      {sc.setting.slice(0, 140)}
                      {sc.setting.length > 140 ? "…" : ""}
                    </div>
                  )}
                  <div style={{ marginTop: 3 }}>
                    {sc.narration && <div>🗣 {sc.narration.slice(0, 120)}</div>}
                    {Object.entries(sc.dialogue).map(([n, line]) => (
                      <div key={n}>
                        💬 <b>{n}:</b> {line.slice(0, 120)}
                      </div>
                    ))}
                    {!sc.narration && Object.keys(sc.dialogue).length === 0 && (
                      <i className="faint">missing dialogue — will be rejected</i>
                    )}
                  </div>
                </div>
              ))}
            </div>
            {preview.valid && (
              <div
                className="faint"
                style={{ fontSize: "0.8rem", marginTop: 10, padding: "8px 10px", background: "rgba(16,185,129,0.12)", borderRadius: 10 }}
              >
                {preview.scenesCount} scenes × 2 runs each = ~{preview.runsNeeded} of your
                daily AI Studio quota. Press “Generate my film” below to start filming.
                <span style={{ marginLeft: 8 }}><StudioQuotaMeter compact /></span>
              </div>
            )}
            {!preview.valid && (
              <div
                className="faint"
                style={{ fontSize: "0.8rem", marginTop: 10, padding: "8px 10px", background: "rgba(248,113,113,0.12)", borderRadius: 10 }}
              >
                Fix the issues above in your paste, or edit the original
                package in ChatGPT and paste it again — then validate once more.
              </div>
            )}
          </Card>
        )}

        {error && (
          <Card>
            <p style={{ color: "var(--danger, #f87171)", margin: 0 }}>{error}</p>
          </Card>
        )}

        <Button
          size="lg"
          onClick={submit}
          disabled={submitting}
          style={{ width: "100%" }}
        >
          {submitting ? (
            <Spinner />
          ) : mode === "podcast" ? (
            "🎙️ Generate my podcast"
          ) : (
            "🎬 Generate my film"
          )}
        </Button>
          </>
        )}
      </div>
    </div>
  );
}