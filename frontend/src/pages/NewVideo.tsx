import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../services/api";
import { Button, Card, Field, Spinner } from "../components/ui";
import { useToast } from "../components/feedback";
import { UsageCard } from "../components/workspace/UsageCard";
import AspectSelect from "../components/AspectSelect";
import type {
  RecipeDraft,
  RecipeField,
  RecipeHandoff,
  RecipeMeta,
  RecipeOutline,
  Workspace,
} from "../types";

const ICONS: Record<string, string> = {
  news: "📰",
  player_story: "🏀",
  game_recap: "📊",
};

function initialValues(fields: RecipeField[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of fields) out[f.name] = f.default === undefined ? "" : String(f.default);
  return out;
}

function fieldInput(
  field: RecipeField,
  value: string,
  onChange: (name: string, value: string) => void
) {
  const common = {
    className: "input",
    value,
    placeholder: field.placeholder,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      onChange(field.name, e.target.value),
  };
  if (field.type === "textarea") {
    return <textarea {...common} className="textarea" rows={3} />;
  }
  if (field.type === "number") {
    return <input {...common} type="number" min={field.min} max={field.max} />;
  }
  return <input {...common} type={field.type === "url" ? "url" : "text"} />;
}

export default function NewVideo() {
  const navigate = useNavigate();
  const toast = useToast();

  const [recipes, setRecipes] = useState<RecipeMeta[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [aspect, setAspect] = useState("");
  const [activeId, setActiveId] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [drafting, setDrafting] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState<RecipeDraft | null>(null);

  useEffect(() => {
    Promise.allSettled([api.listRecipes(), api.listWorkspaces()]).then(([r, w]) => {
      if (r.status === "fulfilled") setRecipes(r.value.items || []);
      if (w.status === "fulfilled") {
        const items = w.value.items || [];
        setWorkspaces(items);
        setWorkspaceId(items[0]?.workspace_id || "");
        setAspect(String(items[0]?.defaults?.aspect || ""));
      }
    });
  }, []);

  const active = useMemo(
    () => recipes.find((r) => r.id === activeId) || null,
    [recipes, activeId]
  );
  const workspace = workspaces.find((w) => w.workspace_id === workspaceId);

  function choose(recipe: RecipeMeta) {
    setActiveId(recipe.id);
    setValues(initialValues(recipe.fields));
    setDraft(null);
    setError("");
  }

  function setValue(name: string, value: string) {
    setValues((prev) => ({ ...prev, [name]: value }));
  }

  function payload(): Record<string, unknown> {
    if (!active) return {};
    const out: Record<string, unknown> = {};
    for (const f of active.fields) {
      const raw = values[f.name] ?? "";
      if (raw === "") continue;
      out[f.name] = f.type === "number" ? Number(raw) : raw;
    }
    return out;
  }

  async function makeDraft() {
    if (!active) return;
    const body = payload();
    const missing = active.fields.filter((f) => f.required && !String(body[f.name] ?? "").trim());
    if (missing.length) {
      setError(`Add ${missing.map((m) => m.label.toLowerCase()).join(", ")}.`);
      return;
    }
    setDrafting(true);
    setError("");
    try {
      const r = await api.draftRecipe(active.id, {
        workspace_id: workspaceId,
        aspect,
        payload: body,
      });
      setDraft(r);
      toast("Draft ready — review it before spending GPU.", "success");
    } catch (e) {
      setError((e as Error).message || "Could not draft this video.");
    } finally {
      setDrafting(false);
    }
  }

  function patchScene(index: number, patch: Partial<RecipeOutline["scenes"][number]>) {
    setDraft((prev) => {
      if (!prev) return prev;
      const scenes = prev.outline.scenes.map((s, i) => (i === index ? { ...s, ...patch } : s));
      return { ...prev, outline: { ...prev.outline, scenes } };
    });
  }

  function patchTitle(title: string) {
    setDraft((prev) =>
      prev ? { ...prev, outline: { ...prev.outline, title } } : prev
    );
  }

  function openInEditor() {
    if (!draft) return;
    const handoff: RecipeHandoff = {
      recipe_id: draft.recipe_id,
      outline: draft.outline,
      defaults: draft.defaults,
      workspace: draft.workspace,
      estimate: draft.estimate,
    };
    navigate("/editor", { state: { draft: handoff } });
  }

  async function renderNow() {
    if (!draft) return;
    if (draft.issues.length) {
      toast("Fix the plan issues in the Editor first.", "error");
      return;
    }
    const outline = draft.outline;
    const settings = draft.defaults;
    setRendering(true);
    setError("");
    try {
      const res = await api.createFilm({
        idea: "",
        outline: JSON.stringify(outline),
        style: outline.style || "sports",
        scenes_count: outline.scenes.length,
        characters: outline.characters,
        captions: settings.captions !== false,
        music: String(settings.music || ""),
        music_volume: Number(settings.music_volume ?? 0.35),
        research: false,
        voice: String(settings.voice || ""),
        aspect: aspect || String(settings.aspect || "auto"),
        workspace_id: workspaceId,
        lip_sync: false,
      });
      toast("Render queued.", "success");
      navigate(`/job/${res.job_id}`);
    } catch (e) {
      setError((e as Error).message || "Could not start the render.");
    } finally {
      setRendering(false);
    }
  }

  return (
    <div className="fade-up" style={{ paddingBottom: 28 }}>
      <header className="spread" style={{ marginBottom: 16, gap: 12, flexWrap: "wrap" }}>
        <div style={{ minWidth: 240 }}>
          <h2 style={{ margin: 0 }}>New video</h2>
          <p className="faint" style={{ fontSize: "0.86rem", margin: "2px 0 0" }}>
            Pick a recipe, get a plan. Drafting is free — GPU only when you render.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Link to="/create" className="btn btn-ghost">
            Blank timeline
          </Link>
          <Link to="/film?mode=film" className="btn btn-ghost">
            Cinematic film
          </Link>
          <Link to="/workspace" className="btn btn-ghost">
            Workspace
          </Link>
        </div>
      </header>

      <div style={{ display: "grid", gap: 16, gridTemplateColumns: "minmax(0, 1.4fr) minmax(280px, 0.9fr)" }}>
        <div style={{ display: "grid", gap: 14, minWidth: 0 }}>
          <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))" }}>
            {recipes.map((r) => (
              <Card
                key={r.id}
                onClick={() => choose(r)}
                className={activeId === r.id ? "card-click" : "card-click"}
                style={{
                  cursor: "pointer",
                  borderColor: activeId === r.id ? "var(--accent)" : undefined,
                  background: activeId === r.id ? "rgba(110,168,254,0.10)" : undefined,
                }}
              >
                <div style={{ fontSize: "1.5rem" }}>{ICONS[r.id] || "🎬"}</div>
                <div style={{ fontWeight: 800, marginTop: 4 }}>{r.name}</div>
                <p className="faint" style={{ margin: "4px 0 0", fontSize: "0.8rem" }}>
                  {r.description}
                </p>
              </Card>
            ))}
            {!recipes.length && (
              <Card>
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <Spinner small />
                  <span className="faint">Loading recipes…</span>
                </div>
              </Card>
            )}
          </div>

          {active && (
            <Card>
              <div className="spread" style={{ marginBottom: 12 }}>
                <h3 className="section-title" style={{ margin: 0 }}>
                  {ICONS[active.id] || "🎬"} {active.name}
                </h3>
                {workspaces.length > 1 && (
                  <select
                    className="input"
                    style={{ maxWidth: 220 }}
                    value={workspaceId}
                    onChange={(e) => setWorkspaceId(e.target.value)}
                  >
                    {workspaces.map((w) => (
                      <option key={w.workspace_id} value={w.workspace_id}>
                        {w.name}
                      </option>
                    ))}
                  </select>
                )}
              </div>

              <div style={{ display: "grid", gap: 10 }}>
                {active.fields.map((f) => (
                  <Field key={f.name} label={`${f.label}${f.required ? " *" : ""}`}>
                    {fieldInput(f, values[f.name] ?? "", setValue)}
                  </Field>
                ))}
                <Field label="Output shape">
                  <AspectSelect value={aspect} onChange={setAspect} />
                </Field>
              </div>

              {error && (
                <p style={{ color: "var(--danger)", fontSize: "0.84rem", margin: "10px 0 0" }}>
                  {error}
                </p>
              )}

              <div className="spread" style={{ marginTop: 14, gap: 10 }}>
                <span className="faint" style={{ fontSize: "0.78rem" }}>
                  Drafting uses a text model only. Your workspace defaults fill voice,
                  captions and music.
                </span>
                <Button variant="primary" onClick={makeDraft} loading={drafting}>
                  Draft video
                </Button>
              </div>
            </Card>
          )}

          {draft && (
            <Card>
              <div className="spread" style={{ gap: 10, flexWrap: "wrap" }}>
                <div style={{ minWidth: 200, flex: 1 }}>
                  <Field label="Title">
                    <input
                      className="input"
                      value={draft.outline.title}
                      onChange={(e) => patchTitle(e.target.value)}
                    />
                  </Field>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div className="faint" style={{ fontSize: "0.72rem", textTransform: "uppercase" }}>
                    Estimate
                  </div>
                  <div style={{ fontWeight: 800 }}>
                    {draft.estimate.scenes} scenes · {draft.estimate.gpu_runs} GPU runs
                  </div>
                </div>
              </div>

              <div className="spread" style={{ marginTop: 10, gap: 8, flexWrap: "wrap" }}>
                <span className="faint" style={{ fontSize: "0.78rem" }}>
                  Drafted by {draft.provider}
                  {draft.fact_check.checked
                    ? ` · fact-checked${draft.fact_check.corrected ? ` (${draft.fact_check.corrected} corrected)` : ""}`
                    : " · no fact-check"}
                </span>
                {draft.issues.length > 0 && (
                  <span style={{ color: "var(--warning)", fontSize: "0.8rem" }}>
                    {draft.issues.length} issue{draft.issues.length === 1 ? "" : "s"} to fix
                  </span>
                )}
              </div>

              {draft.issues.length > 0 && (
                <ul className="faint" style={{ margin: "8px 0 0 18px", fontSize: "0.8rem" }}>
                  {draft.issues.map((issue, i) => (
                    <li key={i}>{issue}</li>
                  ))}
                </ul>
              )}

              {draft.fact_check.notes.length > 0 && (
                <div style={{ marginTop: 8, display: "grid", gap: 4 }}>
                  {draft.fact_check.notes.slice(0, 4).map((note, i) => (
                    <p key={i} className="faint" style={{ margin: 0, fontSize: "0.78rem" }}>
                      • {note}
                    </p>
                  ))}
                </div>
              )}

              {draft.sources.length > 0 && (
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                  {draft.sources.slice(0, 6).map((s, i) => (
                    <a
                      key={`${s.url}-${i}`}
                      href={s.url}
                      target="_blank"
                      rel="noreferrer"
                      className="badge"
                      style={{ textDecoration: "none" }}
                    >
                      {s.title || new URL(s.url).hostname}
                    </a>
                  ))}
                </div>
              )}

              <div style={{ marginTop: 14, display: "grid", gap: 10 }}>
                {draft.outline.scenes.map((scene, index) => (
                  <div
                    key={index}
                    style={{
                      border: "1px solid var(--line, rgba(255,255,255,0.12))",
                      borderRadius: 10,
                      padding: 10,
                      display: "grid",
                      gap: 8,
                    }}
                  >
                    <div className="spread">
                      <b style={{ fontSize: "0.84rem" }}>Scene {index + 1}</b>
                      <span className="faint" style={{ fontSize: "0.74rem" }}>
                        {scene.transition || "default transition"}
                      </span>
                    </div>
                    <textarea
                      className="textarea"
                      rows={2}
                      value={scene.narration}
                      onChange={(e) => patchScene(index, { narration: e.target.value })}
                    />
                    <textarea
                      className="textarea"
                      rows={2}
                      placeholder="Visual prompt"
                      value={scene.setting}
                      onChange={(e) => patchScene(index, { setting: e.target.value })}
                    />
                    <input
                      className="input"
                      value={scene.shot || ""}
                      placeholder="Shot (e.g. slow push-in)"
                      onChange={(e) => patchScene(index, { shot: e.target.value })}
                    />
                  </div>
                ))}
              </div>

              <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
                <Button onClick={openInEditor}>Open in Editor</Button>
                <Button
                  variant="primary"
                  onClick={renderNow}
                  loading={rendering}
                  disabled={draft.issues.length > 0}
                >
                  Render AI film
                </Button>
              </div>
            </Card>
          )}
        </div>

        <div style={{ display: "grid", gap: 14, alignContent: "start" }}>
          <UsageCard
            workspaceId={workspaceId}
            dailyRuns={workspace?.budget?.dailyRuns || 0}
            compact
          />
          {workspace && (
            <Card>
              <div className="faint" style={{ fontSize: "0.72rem", textTransform: "uppercase", letterSpacing: "0.08em" }}>
                Workspace
              </div>
              <div style={{ fontWeight: 800 }}>{workspace.name}</div>
              <div className="faint" style={{ fontSize: "0.8rem" }}>
                {workspace.page_name || "No page name"}
              </div>
              <div style={{ display: "flex", gap: 6, marginTop: 10 }}>
                <span
                  style={{
                    width: 18,
                    height: 18,
                    borderRadius: 5,
                    background: workspace.brand.primary || "#333",
                  }}
                />
                <span
                  style={{
                    width: 18,
                    height: 18,
                    borderRadius: 5,
                    background: workspace.brand.secondary || "#111",
                  }}
                />
                <span className="faint" style={{ fontSize: "0.78rem" }}>
                  {String(workspace.defaults.aspect || "9:16")}
                </span>
              </div>
              <Link to="/workspace" className="btn btn-ghost" style={{ marginTop: 12 }}>
                Edit defaults
              </Link>
            </Card>
          )}
          <Card>
            <div style={{ fontWeight: 800, fontSize: "0.9rem" }}>How it spends</div>
            <p className="faint" style={{ margin: "6px 0 0", fontSize: "0.8rem" }}>
              A draft is free. Rendering costs about two GPU runs per scene; a
              cloned narrator adds one. You see the run count before you commit.
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}
