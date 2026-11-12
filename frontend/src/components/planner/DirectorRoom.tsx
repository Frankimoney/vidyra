import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../services/api";
import type { PlanningProject, ProjectPlan, SavedCharacter } from "../../services/api";
import type { CloneVoiceOption } from "../../hooks/useCloneVoices";
import { useToast } from "../feedback";
import { Button, Card, Field, Spinner, Toggle } from "../ui";
import CharacterMentions from "../CharacterMentions";
import AspectSelect from "../AspectSelect";
import VoiceSelect from "../VoiceSelect";
import { StudioQuotaMeter } from "../StudioQuotaMeter";

const PLAN_STYLES = [
  "cinematic",
  "anime",
  "comedy",
  "horror",
  "sci-fi",
  "fantasy",
  "sports",
  "documentary",
];

const TRANSITIONS = [
  "",
  "fade",
  "slideleft",
  "slideright",
  "wipeleft",
  "wiperight",
  "zoomin",
];

interface Props {
  aspect: string;
  onAspectChange: (v: string) => void;
  captions: boolean;
  onCaptionsChange: (v: boolean) => void;
  research: boolean;
  music: string;
  musicVolume: number;
  voice: string;
  onVoiceChange: (v: string) => void;
  voices: string[];
  cloneVoices: CloneVoiceOption[];
  /** Reopen this project on mount, so a shared/deep link lands in the thread. */
  initialProjectId?: string;
}

export default function DirectorRoom(props: Props) {
  const { aspect, onAspectChange } = props;
  const toast = useToast();
  const navigate = useNavigate();

  const [projects, setProjects] = useState<PlanningProject[]>([]);
  const [project, setProject] = useState<PlanningProject | null>(null);
  const [input, setInput] = useState("");
  const [starting, setStarting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState<ProjectPlan | null>(null);
  const [dirty, setDirty] = useState(false);
  const [showProjects, setShowProjects] = useState(false);
  // The user's saved cast, so planned characters can reuse a locked face.
  const [cast, setCast] = useState<SavedCharacter[]>([]);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    api
      .listProjects()
      .then((r) => setProjects(r.items || []))
      .catch(() => setProjects([]));
  }, []);

  useEffect(() => {
    api
      .listCharacters()
      .then(setCast)
      .catch(() => setCast([]));
  }, []);

  // Deep link straight into a project (the front door sends people here after
  // starting a session). Runs once so it can't fight their own navigation.
  const reopened = useRef(false);
  useEffect(() => {
    const id = props.initialProjectId;
    if (!id || reopened.current) return;
    reopened.current = true;
    setStarting(true);
    api
      .getProject(id)
      .then((r) => adopt(r.project))
      .catch(() =>
        setError("We couldn't reopen that project. Describe a new one below.")
      )
      .finally(() => setStarting(false));
  }, [props.initialProjectId]);

  // Seed the editor from the server plan, but never clobber in-progress edits:
  // the seed key only changes when the backend actually sends a new plan.
  const planKey = project?.plan ? JSON.stringify(project.plan) : "";
  const seededKey = useRef("");
  useEffect(() => {
    if (project?.plan && seededKey.current !== planKey) {
      seededKey.current = planKey;
      setDraft(project.plan);
      setDirty(false);
    }
  }, [planKey, project?.plan]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [project?.messages.length, project?.status]);

  function adopt(next: PlanningProject) {
    setProject(next);
    setProjects((prev) => {
      const rest = prev.filter((p) => p.projectId !== next.projectId);
      return [next, ...rest];
    });
  }

  async function start(message: string) {
    const text = message.trim();
    if (text.length < 3) {
      toast("Describe your film in a sentence or two.", "error");
      return;
    }
    setStarting(true);
    setError("");
    try {
      const r = await api.createProject(text, aspect);
      seededKey.current = "";
      setDraft(null);
      setInput("");
      adopt(r.project);
    } catch (e) {
      setError((e as Error).message || "Could not start the session.");
    } finally {
      setStarting(false);
    }
  }

  async function send(message: string) {
    if (!project) return;
    const text = message.trim();
    if (!text) return;
    setBusy(true);
    setError("");
    // Optimistic echo so the thread never feels laggy.
    adopt({
      ...project,
      messages: [...project.messages, { role: "user", content: text }],
    });
    setInput("");
    try {
      const r = await api.sendProjectMessage(project.projectId, text);
      adopt(r.project);
    } catch (e) {
      setError((e as Error).message || "The agent didn't reply.");
    } finally {
      setBusy(false);
    }
  }

  async function savePlan(): Promise<ProjectPlan | null> {
    if (!project || !draft) return null;
    setSaving(true);
    setError("");
    try {
      const r = await api.saveProjectPlan(project.projectId, draft);
      adopt(r.project);
      toast("Plan saved.", "success");
      return r.project.plan;
    } catch (e) {
      setError((e as Error).message || "The plan didn't save.");
      return null;
    } finally {
      setSaving(false);
    }
  }

  async function approve() {
    if (!project || !draft) return;
    if (draft.scenes.length < 2) {
      toast("A film needs at least 2 scenes.", "error");
      return;
    }
    setRendering(true);
    setError("");
    try {
      // Persist first, so the project's memory matches what actually rendered.
      // A project that has already been filmed is locked -- the server refuses
      // to rewrite its plan so the finished film keeps its link -- so filming it
      // again renders the draft as-is instead of overwriting that record.
      const alreadyFilmed = project.status === "filmed" || !!project.jobId;
      const saved = alreadyFilmed
        ? null
        : await api.saveProjectPlan(project.projectId, draft);
      const plan = saved?.project.plan ?? draft;
      const res = await api.createFilm({
        idea: "",
        outline: JSON.stringify(plan),
        style: plan.style || "cinematic",
        scenes_count: plan.scenes.length,
        characters: [],
        captions: props.captions,
        music: props.music,
        music_volume: props.musicVolume,
        research: props.research,
        voice: props.voice,
        aspect,
        project_id: project.projectId,
      });
      await api.markProjectFilmed(project.projectId, res.job_id).catch(() => {});
      toast("Plan approved — filming now.", "success");
      navigate(`/job/${res.job_id}`);
    } catch (e) {
      setError((e as Error).message || "Could not start filming.");
      setRendering(false);
    }
  }

  async function removeProject(id: string) {
    try {
      await api.deleteProject(id);
      setProjects((prev) => prev.filter((p) => p.projectId !== id));
      if (project?.projectId === id) {
        setProject(null);
        setDraft(null);
        seededKey.current = "";
      }
      toast("Project deleted.", "success");
    } catch (e) {
      setError((e as Error).message || "Could not delete the project.");
    }
  }

  function editPlan(fn: (p: ProjectPlan) => ProjectPlan) {
    setDraft((d) => (d ? fn(d) : d));
    setDirty(true);
  }

  const styleOptions =
    draft && !PLAN_STYLES.includes(draft.style)
      ? [draft.style, ...PLAN_STYLES]
      : PLAN_STYLES;

  // The plan's characters and the project's cast are the same identities, so
  // the editor shows which looks are already locked for this project.
  const castByName = new Map(
    (project?.cast ?? []).map((c) => [c.name.trim().toLowerCase(), c])
  );

  return (
    <div style={{ display: "grid", gap: 18 }}>
      <Card>
        <div className="spread" style={{ alignItems: "flex-start" }}>
          <div>
            <div style={{ fontWeight: 700 }}>🎬 Director</div>
            <div className="faint" style={{ fontSize: "0.85rem", marginTop: 4 }}>
              Talk through your film first. Questions are free — GPU renders
              aren&apos;t. You get an editable plan before anything is filmed.
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <StudioQuotaMeter compact />
            <Button variant="ghost" onClick={() => setShowProjects((v) => !v)}>
              {showProjects ? "Hide" : "Projects"} ({projects.length})
            </Button>
          </div>
        </div>

        {showProjects && (
          <div style={{ display: "grid", gap: 6, marginTop: 12 }}>
            {projects.length === 0 && (
              <div className="faint" style={{ fontSize: "0.85rem" }}>
                No projects yet. Describe a film below to start one.
              </div>
            )}
            {projects.map((p) => (
              <div
                key={p.projectId}
                className="input spread"
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr auto",
                  gap: 8,
                  alignItems: "center",
                  padding: 8,
                }}
              >
                <button
                  type="button"
                  onClick={async () => {
                    const r = await api.getProject(p.projectId);
                    seededKey.current = "";
                    setDraft(null);
                    adopt(r.project);
                  }}
                  style={{
                    background: "none",
                    border: "none",
                    color: "var(--text)",
                    textAlign: "left",
                    cursor: "pointer",
                    font: "inherit",
                  }}
                >
                  <div style={{ fontWeight: 600, fontSize: "0.9rem" }}>{p.title}</div>
                  <div className="faint" style={{ fontSize: "0.76rem" }}>
                    {p.status === "planning" && "planning"}
                    {p.status === "planned" &&
                      `plan ready · ${p.scenesCount} scenes · about ${p.runsNeeded} AI runs`}
                    {p.status === "filmed" && "filmed"}
                    {p.jobId ? ` · job ${p.jobId.slice(0, 6)}` : ""}
                  </div>
                </button>
                <Button variant="ghost" onClick={() => removeProject(p.projectId)}>
                  ✕
                </Button>
              </div>
            ))}
          </div>
        )}

        {!project && (
          <div style={{ marginTop: 12 }}>
            <Field label="What is your film about?">
              <CharacterMentions
                className="input"
                rows={3}
                maxLength={8000}
                placeholder="e.g. A young inventor on a rainy island finds a machine that talks to birds — and must use it to save her town."
                value={input}
                onChange={setInput}
                characters={cast}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) start(input);
                }}
              />
              {cast.length > 0 && (
                <p className="faint" style={{ fontSize: "0.76rem", margin: "4px 0 0" }}>
                  Type @ to bring in a saved character.
                </p>
              )}
            </Field>
            <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
              <Button size="lg" onClick={() => start(input)} disabled={starting}>
                {starting ? <Spinner /> : "🎬 Start planning"}
              </Button>
            </div>
          </div>
        )}
      </Card>

      {project && (
        <Card>
          <div className="spread" style={{ marginBottom: 10 }}>
            <div style={{ fontWeight: 700, fontSize: "0.95rem" }}>
              {project.status === "planned" || project.status === "filmed"
                ? "Production plan"
                : "Planning"}
            </div>
            <Button
              variant="ghost"
              onClick={() => {
                setProject(null);
                setDraft(null);
                seededKey.current = "";
                setInput("");
              }}
            >
              ✎ New project
            </Button>
          </div>

          <div
            style={{
              display: "grid",
              gap: 8,
              maxHeight: 300,
              overflowY: "auto",
              paddingRight: 4,
            }}
          >
            {project.messages.map((m, i) => (
              <div
                key={i}
                style={{
                  justifySelf: m.role === "user" ? "end" : "start",
                  maxWidth: "92%",
                  padding: "8px 11px",
                  borderRadius: 12,
                  background:
                    m.role === "user"
                      ? "var(--primary-strong, #6366f1)"
                      : "rgba(255,255,255,0.05)",
                  fontSize: "0.88rem",
                  whiteSpace: "pre-wrap",
                }}
              >
                {m.content}
              </div>
            ))}
            {busy && <div className="faint">Director is thinking…</div>}
            <div ref={endRef} />
          </div>

          {project.questions.length > 0 && (
            <div style={{ marginTop: 10 }}>
              {project.questions.map((q, i) => (
                <div key={i} className="faint" style={{ fontSize: "0.85rem" }}>
                  • {q}
                </div>
              ))}
            </div>
          )}

          <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
            {project.status === "planning" && (
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <Button variant="ghost" onClick={() => send("Plan it now.")} disabled={busy}>
                  Plan it now
                </Button>
                <Button variant="ghost" onClick={() => send("Make it shorter.")} disabled={busy}>
                  Shorter
                </Button>
              </div>
            )}
            <CharacterMentions
              className="input"
              rows={2}
              maxLength={8000}
              placeholder={
                project.plan
                  ? "Ask for a change, or ask a question about the plan…"
                  : "Answer the questions, or describe what you want…"
              }
              value={input}
              onChange={setInput}
              characters={cast}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send(input);
                }
              }}
            />
          </div>
        </Card>
      )}

      {draft && (
        <Card>
          <div className="spread" style={{ alignItems: "flex-start", marginBottom: 10 }}>
            <div>
              <div style={{ fontWeight: 700 }}>Your plan</div>
              <div className="faint" style={{ fontSize: "0.85rem" }}>
                {draft.scenes.length} scenes · about {project?.runsNeeded ?? 0} AI runs.
                Change anything — this is what gets filmed, word for word.
              </div>
            </div>
            <Button variant="ghost" onClick={() => savePlan()} disabled={saving}>
              {saving ? <Spinner small /> : dirty ? "Save" : "Saved"}
            </Button>
          </div>

          <Field label="Title">
            <input
              className="input"
              maxLength={80}
              value={draft.title}
              onChange={(e) => editPlan((p) => ({ ...p, title: e.target.value }))}
            />
          </Field>

          <Field label="Look &amp; feel">
            <select
              className="input"
              value={draft.style}
              onChange={(e) => editPlan((p) => ({ ...p, style: e.target.value }))}
            >
              {styleOptions.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Any special instructions (optional)">
            <textarea
              className="input"
              rows={2}
              maxLength={2000}
              value={draft.instructions ?? ""}
              onChange={(e) => editPlan((p) => ({ ...p, instructions: e.target.value }))}
            />
          </Field>

          <div style={{ marginTop: 12 }}>
            <div className="spread" style={{ marginBottom: 6 }}>
              <div style={{ fontWeight: 700, fontSize: "0.9rem" }}>
                Who&apos;s in it
              </div>
<Button
                variant="ghost"
                onClick={() =>
                  editPlan((p) => ({
                    ...p,
                    characters: [
                      ...p.characters,
                      { name: "", description: "" },
                    ],
                  }))
                }
              >
                ＋
              </Button>
            </div>

            {/* Saved cast, offered as one-tap additions so a character taught
                once can be reused without retyping their description. */}
            {cast.length > 0 && (
              <div
                className="spread"
                style={{ gap: 6, marginBottom: 10, flexWrap: "wrap" }}
              >
                <span className="faint" style={{ fontSize: "0.76rem" }}>
                  From your cast:
                </span>
                {cast
                  .filter(
                    (s) =>
                      !draft.characters.some(
                        (c) => c.name.trim().toLowerCase() === s.name.trim().toLowerCase()
                      )
                  )
                  .slice(0, 6)
                  .map((s) => (
                    <button
                      key={s.character_id}
                      type="button"
                      className="input"
                      onClick={() =>
                        editPlan((p) => ({
                          ...p,
                          characters: [
                            ...p.characters,
                            { name: s.name, description: s.description || "" },
                          ],
                        }))
                      }
                      style={{
                        cursor: "pointer",
                        fontSize: "0.78rem",
                        padding: "5px 9px",
                        width: "auto",
                      }}
                    >
                      + {s.name}
                    </button>
                  ))}
              </div>
            )}

            {draft.characters.map((c, i) => (
              <div key={i} style={{ display: "grid", gap: 6, marginBottom: 8 }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 6 }}>
                  <input
                    className="input"
                    placeholder="Name"
                    maxLength={40}
                    value={c.name}
                    onChange={(e) =>
                      editPlan((p) => ({
                        ...p,
                        characters: p.characters.map((x, j) =>
                          j === i ? { ...x, name: e.target.value } : x
                        ),
                      }))
                    }
                  />
                  <Button
                    variant="ghost"
                    onClick={() =>
                      editPlan((p) => ({
                        ...p,
                        characters: p.characters.filter((_, j) => j !== i),
                      }))
                    }
                  >
                    ✕
                  </Button>
                </div>
                <input
                  className="input"
                  placeholder="Appearance — what the AI should draw"
                  maxLength={500}
                  value={c.description}
                  onChange={(e) =>
                    editPlan((p) => ({
                      ...p,
                      characters: p.characters.map((x, j) =>
                        j === i ? { ...x, description: e.target.value } : x
                      ),
                    }))
                  }
                />
                {c.name.trim() && (
                  <div className="faint" style={{ fontSize: "0.74rem" }}>
                    {castByName.get(c.name.trim().toLowerCase())?.portrait
                      ? "🔒 Look locked for this project — reused in every film, no extra GPU run."
                      : "Look gets locked on first render, then reused in every film."}
                  </div>
                )}
              </div>
            ))}
          </div>

          <div style={{ marginTop: 12 }}>
            <div className="spread" style={{ marginBottom: 6 }}>
              <div style={{ fontWeight: 700, fontSize: "0.9rem" }}>
                What happens ({draft.scenes.length} scenes)
              </div>
              <Button
                variant="ghost"
                onClick={() =>
                  editPlan((p) => ({
                    ...p,
                    scenes: [
                      ...p.scenes,
                      { narration: "", dialogue: {}, setting: "", shot: "", transition: "" },
                    ],
                  }))
                }
              >
                    ＋ Scene
              </Button>
            </div>

            {draft.scenes.map((sc, i) => (
              <div
                key={i}
                className="input"
                style={{ display: "grid", gap: 6, marginBottom: 10, padding: 10 }}
              >
                <div className="spread" style={{ alignItems: "center" }}>
                  <b style={{ fontSize: "0.88rem" }}>Scene {i + 1}</b>
                  <span style={{ display: "flex", gap: 4 }}>
                    <Button
                      variant="ghost"
                      disabled={i === 0}
                      onClick={() =>
                        editPlan((p) => {
                          const next = [...p.scenes];
                          [next[i - 1], next[i]] = [next[i], next[i - 1]];
                          return { ...p, scenes: next };
                        })
                      }
                    >
                      ↑
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={i === draft.scenes.length - 1}
                      onClick={() =>
                        editPlan((p) => {
                          const next = [...p.scenes];
                          [next[i + 1], next[i]] = [next[i], next[i + 1]];
                          return { ...p, scenes: next };
                        })
                      }
                    >
                      ↓
                    </Button>
                    <Button
                      variant="ghost"
                      onClick={() =>
                        editPlan((p) => ({
                          ...p,
                          scenes: p.scenes.filter((_, j) => j !== i),
                        }))
                      }
                    >
                      ✕
                    </Button>
                  </span>
                </div>

                <textarea
                  className="input"
                  rows={2}
                  placeholder="Where this happens — place, people, lighting"
                  maxLength={2000}
                  value={sc.setting}
                  onChange={(e) =>
                    editPlan((p) => ({
                      ...p,
                      scenes: p.scenes.map((x, j) =>
                        j === i ? { ...x, setting: e.target.value } : x
                      ),
                    }))
                  }
                />
                <input
                  className="input"
                  placeholder="Camera move (optional) — e.g. slow push-in"
                  maxLength={200}
                  value={sc.shot}
                  onChange={(e) =>
                    editPlan((p) => ({
                      ...p,
                      scenes: p.scenes.map((x, j) =>
                        j === i ? { ...x, shot: e.target.value } : x
                      ),
                    }))
                  }
                />
                <textarea
                  className="input"
                  rows={2}
                  placeholder="Voice-over for this scene"
                  maxLength={2000}
                  value={sc.narration}
                  onChange={(e) =>
                    editPlan((p) => ({
                      ...p,
                      scenes: p.scenes.map((x, j) =>
                        j === i ? { ...x, narration: e.target.value } : x
                      ),
                    }))
                  }
                />

                {Object.entries(sc.dialogue || {}).map(([who, line], k) => (
                  <div key={k} style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 6 }}>
                    <input
                      className="input"
                      placeholder={`${who} says…`}
                      maxLength={2000}
                      value={line}
                      onChange={(e) =>
                        editPlan((p) => ({
                          ...p,
                          scenes: p.scenes.map((x, j) =>
                            j === i
                              ? {
                                  ...x,
                                  dialogue: { ...(x.dialogue || {}), [who]: e.target.value },
                                }
                              : x
                          ),
                        }))
                      }
                    />
                    <Button
                      variant="ghost"
                      onClick={() =>
                        editPlan((p) => ({
                          ...p,
                          scenes: p.scenes.map((x, j) => {
                            if (j !== i) return x;
                            const d = { ...(x.dialogue || {}) };
                            delete d[who];
                            return { ...x, dialogue: d };
                          }),
                        }))
                      }
                    >
                      ✕
                    </Button>
                  </div>
                ))}

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
                  <select
                    className="input"
                    value=""
                    onChange={(e) => {
                      const who = e.target.value;
                      if (!who) return;
                      editPlan((p) => ({
                        ...p,
                        scenes: p.scenes.map((x, j) =>
                          j === i
                            ? {
                                ...x,
                                dialogue: {
                                  ...(x.dialogue || {}),
                                  [who]: (x.dialogue || {})[who] || "",
                                },
                              }
                            : x
                        ),
                      }));
                    }}
                  >
                    <option value="">+ spoken line…</option>
                    {draft.characters
                      .filter((c) => c.name.trim())
                      .map((c) => (
                        <option key={c.name} value={c.name}>
                          {c.name}
                        </option>
                      ))}
                  </select>
                  <select
                    className="input"
                    value={sc.transition ?? ""}
                    onChange={(e) =>
                      editPlan((p) => ({
                        ...p,
                        scenes: p.scenes.map((x, j) =>
                          j === i ? { ...x, transition: e.target.value } : x
                        ),
                      }))
                    }
                  >
                    {TRANSITIONS.map((t) => (
                      <option key={t} value={t}>
                        {t || "transition: auto"}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            ))}
          </div>

          <Field label="Video shape">
            <AspectSelect
              id="director-aspect"
              value={aspect}
              onChange={onAspectChange}
              hint="Vertical is TikTok and Reels. Wide is YouTube. Landscape is safest."
            />
          </Field>

          <div style={{ marginTop: 10, display: "grid", gap: 6 }}>
            <Toggle on={props.captions} onChange={props.onCaptionsChange} label="Captions" />
          </div>

          <Field label="Narrator voice (optional)">
            <VoiceSelect
              value={props.voice}
              onChange={props.onVoiceChange}
              voices={props.voices}
              cloneVoices={props.cloneVoices}
              inheritLabel="Auto — Vidyra picks a voice"
            />
          </Field>
        </Card>
      )}

      {error && (
        <Card>
          <p style={{ color: "var(--danger, #f87171)", margin: 0 }}>{error}</p>
        </Card>
      )}

      {draft && (
        <Button size="lg" onClick={approve} disabled={rendering} style={{ width: "100%" }}>
          {rendering ? <Spinner /> : "🎬 Make my video"}
        </Button>
      )}
    </div>
  );
}