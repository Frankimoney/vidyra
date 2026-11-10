import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import { Button, Card, Field, Spinner } from "../components/ui";
import { useToast } from "../components/feedback";
import { useCloneVoices } from "../hooks/useCloneVoices";
import { UsageCard } from "../components/workspace/UsageCard";
import StepSettings from "../components/create/StepSettings";
import StepVisuals from "../components/create/StepVisuals";
import { DEFAULT_SETTINGS, settingsFrom } from "../utils/settings";
import type { VideoSettings, Workspace as WorkspaceType } from "../types";

interface CatalogState {
  voices: string[];
  music: string[];
  pacing: string[];
  hooks: string[];
  captionStyles: string[];
  captionPositions: string[];
  transitions: string[];
  minRate: number;
  maxRate: number;
}

export default function Workspace() {
  const toast = useToast();
  const cloneVoices = useCloneVoices();

  const [workspace, setWorkspace] = useState<WorkspaceType | null>(null);
  const [settings, setSettings] = useState<VideoSettings | null>(null);
  const [catalog, setCatalog] = useState<CatalogState | null>(null);
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [refresh, setRefresh] = useState(0);

  const [name, setName] = useState("");
  const [pageName, setPageName] = useState("");
  const [niche, setNiche] = useState("");
  const [primary, setPrimary] = useState("");
  const [secondary, setSecondary] = useState("");
  const [dailyRuns, setDailyRuns] = useState(0);
  const [memberEmail, setMemberEmail] = useState("");
  const [memberUid, setMemberUid] = useState("");
  const [memberRole, setMemberRole] = useState<"editor" | "viewer">("editor");
  const [memberBusy, setMemberBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.allSettled([
      api.listWorkspaces(),
      api.catalog(),
      api.catalogVoices(),
      api.catalogMusic(),
    ]).then(([w, presets, voices, music]) => {
      if (!alive) return;
      const ws =
        w.status === "fulfilled" ? (w.value.items || [])[0] || null : null;
      if (!ws) {
        setLoadError("Could not load your workspace. Check the backend and refresh.");
        return;
      }
      const p = presets.status === "fulfilled" ? presets.value : null;
      const v = voices.status === "fulfilled" ? voices.value : null;
      const m = music.status === "fulfilled" ? music.value : null;
      const voicesList = v?.voices ?? [];

      setWorkspace(ws);
      setName(ws.name);
      setPageName(ws.page_name || "");
      setNiche(ws.niche || "");
      setPrimary(ws.brand?.primary || "");
      setSecondary(ws.brand?.secondary || "");
      setDailyRuns(ws.budget?.dailyRuns || 0);

      const merged = settingsFrom({
        ...DEFAULT_SETTINGS,
        ...ws.defaults,
        page_name: ws.page_name || (ws.defaults?.page_name as string) || "",
      });
      if (!merged.voice && voicesList.length) merged.voice = voicesList[0];

      setSettings(merged);
      setCatalog({
        voices: voicesList,
        music: m?.music ?? [],
        pacing: p?.pacing ?? ["Breaking News", "Normal Sports Story", "High Energy", "Analysis"],
        hooks: p?.hooks ?? ["Breaking News", "Question", "Bold Statement", "Debate", "Game Day"],
        captionStyles: p?.caption_styles ?? ["Karaoke", "Clean", "Bold"],
        captionPositions: p?.caption_positions ?? ["Top", "Center", "Bottom"],
        transitions: p?.transitions ?? ["Auto", "fade", "slideleft", "slideright", "wipeleft", "wiperight"],
        minRate: p?.speech_rate?.min ?? 0.85,
        maxRate: p?.speech_rate?.max ?? 1.15,
      });
    });
    return () => {
      alive = false;
    };
  }, []);

  function patch(p: Partial<VideoSettings>) {
    setSettings((prev) => (prev ? { ...prev, ...p } : prev));
  }

  async function save() {
    if (!workspace || !settings) return;
    setSaving(true);
    try {
      const updated = await api.updateWorkspace(workspace.workspace_id, {
        name,
        page_name: pageName,
        niche,
        brand: { primary, secondary, logoPath: workspace.brand?.logoPath || "" },
        daily_runs: Math.max(0, Math.round(dailyRuns)),
        defaults: { ...settings, music: settings.music || "" },
      });
      setWorkspace(updated);
      setRefresh((n) => n + 1);
      toast("Workspace saved. New videos inherit these defaults.", "success");
    } catch (e) {
      toast((e as Error).message || "Could not save the workspace.", "error");
    } finally {
      setSaving(false);
    }
  }

  async function addMember() {
    if (!workspace) return;
    if (!memberEmail.trim() && !memberUid.trim()) {
      toast("Add an email or user id.", "error");
      return;
    }
    setMemberBusy(true);
    try {
      const updated = await api.addWorkspaceMember(workspace.workspace_id, {
        email: memberEmail.trim() || undefined,
        uid: memberUid.trim() || undefined,
        role: memberRole,
      });
      setWorkspace(updated);
      setMemberEmail("");
      setMemberUid("");
      toast("Member added.", "success");
    } catch (e) {
      toast((e as Error).message || "Could not add that member.", "error");
    } finally {
      setMemberBusy(false);
    }
  }

  async function removeMember(uid: string) {
    if (!workspace) return;
    setMemberBusy(true);
    try {
      const updated = await api.removeWorkspaceMember(workspace.workspace_id, uid);
      setWorkspace(updated);
      toast("Member removed.", "success");
    } catch (e) {
      toast((e as Error).message || "Could not remove that member.", "error");
    } finally {
      setMemberBusy(false);
    }
  }

  if (loadError) {
    return (
      <Card>
        <p style={{ margin: 0 }}>{loadError}</p>
      </Card>
    );
  }

  if (!workspace || !settings || !catalog) {
    return (
      <div style={{ display: "grid", placeItems: "center", minHeight: "40dvh" }}>
        <Spinner />
      </div>
    );
  }

  return (
    <div className="fade-up" style={{ paddingBottom: 28 }}>
      <header className="spread" style={{ marginBottom: 16, gap: 12, flexWrap: "wrap" }}>
        <div style={{ minWidth: 240 }}>
          <h2 style={{ margin: 0 }}>Workspace</h2>
          <p className="faint" style={{ fontSize: "0.86rem", margin: "2px 0 0" }}>
            The brand and defaults every new video inherits.
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <Link to="/new" className="btn btn-ghost">
            New video
          </Link>
          <Button variant="primary" onClick={save} loading={saving}>
            Save workspace
          </Button>
        </div>
      </header>

      <div style={{ display: "grid", gap: 16, gridTemplateColumns: "minmax(0, 1.3fr) minmax(260px, 0.8fr)" }}>
        <div style={{ display: "grid", gap: 14, minWidth: 0 }}>
          <Card>
            <h3 className="section-title" style={{ marginTop: 0 }}>
              Brand
            </h3>
            <div className="grid-2">
              <Field label="Workspace name">
                <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
              <Field label="Page name (on videos)">
                <input className="input" value={pageName} onChange={(e) => setPageName(e.target.value)} />
              </Field>
            </div>
            <div className="grid-2" style={{ marginTop: 10 }}>
              <Field label="Niche">
                <input
                  className="input"
                  value={niche}
                  placeholder="e.g. sports"
                  onChange={(e) => setNiche(e.target.value)}
                />
              </Field>
              <Field label="Daily GPU run cap (0 = unlimited)">
                <input
                  className="input"
                  type="number"
                  min={0}
                  value={dailyRuns}
                  onChange={(e) => setDailyRuns(Number(e.target.value) || 0)}
                />
              </Field>
            </div>
            <div className="grid-2" style={{ marginTop: 10 }}>
              <Field label="Primary colour">
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    type="color"
                    value={primary || "#73000A"}
                    onChange={(e) => setPrimary(e.target.value)}
                    style={{ width: 42, height: 34, border: "none", background: "none" }}
                  />
                  <input
                    className="input"
                    value={primary}
                    placeholder="#73000A"
                    onChange={(e) => setPrimary(e.target.value)}
                  />
                </div>
              </Field>
              <Field label="Secondary colour">
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <input
                    type="color"
                    value={secondary || "#000000"}
                    onChange={(e) => setSecondary(e.target.value)}
                    style={{ width: 42, height: 34, border: "none", background: "none" }}
                  />
                  <input
                    className="input"
                    value={secondary}
                    placeholder="#000000"
                    onChange={(e) => setSecondary(e.target.value)}
                  />
                </div>
              </Field>
            </div>
          </Card>

          <Card>
            <h3 className="section-title" style={{ marginTop: 0 }}>
              Default look &amp; sound
            </h3>
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
            <div className="divider" />
            <StepVisuals
              settings={settings}
              onUpdate={patch}
              captionStyles={catalog.captionStyles}
              captionPositions={catalog.captionPositions}
              transitions={catalog.transitions}
              music={catalog.music}
              pageName={pageName}
            />
          </Card>
        </div>

        <div style={{ display: "grid", gap: 14, alignContent: "start" }}>
          <UsageCard
            workspaceId={workspace.workspace_id}
            dailyRuns={workspace.budget?.dailyRuns || 0}
            refreshKey={refresh}
          />
          <Card>
            <div style={{ fontWeight: 800, fontSize: "0.9rem", marginBottom: 8 }}>Team</div>
            <div style={{ display: "grid", gap: 6 }}>
              {(workspace.members || []).map((m) => (
                <div key={m.uid} className="spread" style={{ gap: 8, fontSize: "0.82rem" }}>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {m.email || m.uid}
                  </span>
                  <span className="faint">{m.role}</span>
                  <button
                    type="button"
                    onClick={() => removeMember(m.uid)}
                    disabled={memberBusy}
                    style={{ background: "none", border: "none", color: "var(--text-3)", cursor: "pointer" }}
                    title="Remove member"
                  >
                    ✕
                  </button>
                </div>
              ))}
              {(workspace.members || []).length === 0 && (
                <p className="faint" style={{ margin: 0, fontSize: "0.8rem" }}>
                  Only you. Add an editor or reviewer below.
                </p>
              )}
            </div>
            <div style={{ display: "grid", gap: 6, marginTop: 10 }}>
              <input className="input" placeholder="Email" value={memberEmail} onChange={(e) => setMemberEmail(e.target.value)} />
              <input className="input" placeholder="or user id (dev)" value={memberUid} onChange={(e) => setMemberUid(e.target.value)} />
              <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 6 }}>
                <select className="input" value={memberRole} onChange={(e) => setMemberRole(e.target.value as "editor" | "viewer")}>
                  <option value="editor">Editor</option>
                  <option value="viewer">Viewer</option>
                </select>
                <Button onClick={addMember} loading={memberBusy}>Add</Button>
              </div>
            </div>
          </Card>

          <Card>
            <div style={{ fontWeight: 800, fontSize: "0.9rem" }}>What inherits</div>
            <p className="faint" style={{ margin: "6px 0 0", fontSize: "0.82rem" }}>
              Recipes, Auto Film and timeline jobs all start from these defaults.
              A scene or recipe value always wins, then the workspace, then the app.
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}
