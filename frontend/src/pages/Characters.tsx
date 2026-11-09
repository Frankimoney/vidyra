import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import type { SavedCharacter } from "../services/api";
import { Button, Card, EmptyState, Field, Spinner } from "../components/ui";
import { useToast } from "../components/feedback";

/**
 * The Cast — teach Vidyra a character once and it stays the same in every
 * video. A photo becomes the character's locked face; Auto Film, Storyboard,
 * Studio and the Director all reuse it instead of inventing a new one.
 */
export default function Characters() {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement | null>(null);

  const [items, setItems] = useState<SavedCharacter[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [uploading, setUploading] = useState(false);
  const [pendingPhoto, setPendingPhoto] = useState<string | null>(null);
  const [busyId, setBusyId] = useState("");

  const load = useCallback(async () => {
    try {
      setItems(await api.listCharacters());
    } catch {
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Preview URLs are signed and short-lived, so they're resolved per render
  // rather than stored on the character record.
  const [previews, setPreviews] = useState<Record<string, string>>({});
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const out: Record<string, string> = {};
      for (const c of items) {
        if (!c.ref_image) continue;
        try {
          const r = await api.preview(c.ref_image);
          out[c.character_id] = r.url;
        } catch {
          /* a character can exist before its photo is locked */
        }
      }
      if (!cancelled) setPreviews(out);
    })();
    return () => {
      cancelled = true;
    };
  }, [items]);

  async function onPick(file: File | undefined) {
    if (!file) return;
    setUploading(true);
    try {
      // Reuse the normal upload pipeline so the file lands in the user's own
      // storage and passes the same ownership checks as any other media.
      const up = await api.uploadFile(file);
      const path = up.items?.[0]?.storagePath || "";
      if (!path) throw new Error("Upload did not return a file.");
      setPendingPhoto(path);
      if (!name.trim()) setName(file.name.replace(/\.[^.]+$/, "").slice(0, 40));
    } catch (e) {
      toast((e as Error).message || "That photo did not upload.", "error");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function save() {
    const trimmed = name.trim();
    if (!trimmed) {
      toast("Give your character a name.", "error");
      return;
    }
    setBusyId("new");
    try {
      await api.saveCharacter({
        name: trimmed,
        description: description.trim(),
        ref_image: pendingPhoto || "",
      });
      setName("");
      setDescription("");
      setPendingPhoto("");
      await load();
      toast(`${trimmed} is in your cast.`, "success");
    } catch (e) {
      toast((e as Error).message || "Could not save that character.", "error");
    } finally {
      setBusyId("");
    }
  }

  async function remove(c: SavedCharacter) {
    setBusyId(c.character_id);
    try {
      await api.deleteCharacter(c.character_id);
      await load();
      toast(`${c.name} removed.`, "success");
    } catch (e) {
      toast((e as Error).message || "Could not remove that character.", "error");
    } finally {
      setBusyId("");
    }
  }

  return (
    <div className="fade-up">
      <header style={{ marginBottom: 18 }}>
        <h2 style={{ margin: 0 }}>Your cast</h2>
        <p className="tagline" style={{ margin: "6px 0 0" }}>
          Teach Vidyra a character once and they look the same in every video you make.
          Add a photo and it becomes their face everywhere.
        </p>
      </header>

      <Card>
        <div style={{ fontWeight: 700, marginBottom: 2 }}>Add a character</div>
        <div className="faint" style={{ fontSize: "0.82rem", marginBottom: 12 }}>
          A clear, front-facing photo works best.
        </div>

        <input
          ref={fileRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          style={{ display: "none" }}
          onChange={(e) => void onPick(e.target.files?.[0])}
        />
        <button
          type="button"
          className="input"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          style={{
            cursor: uploading ? "wait" : "pointer",
            width: "100%",
            padding: 18,
            textAlign: "center",
            marginBottom: 12,
          }}
        >
          {uploading ? (
            <span style={{ display: "flex", justifyContent: "center" }}>
              <Spinner small /> Uploading…
            </span>
          ) : pendingPhoto ? (
            <span style={{ color: "var(--success)" }}>✓ Photo ready — click to swap</span>
          ) : (
            <span>+ Choose a photo</span>
          )}
        </button>

        <Field label="Name">
          <input
            className="input"
            maxLength={40}
            placeholder="e.g. Biscuit the cat"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field label="How should they look? (optional)">
          <input
            className="input"
            maxLength={500}
            placeholder="e.g. orange tabby, green eyes, always wears a red collar"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </Field>

        <Button
          variant="primary"
          onClick={save}
          disabled={busyId === "new" || !name.trim()}
          style={{ width: "100%" }}
        >
          {busyId === "new" ? <Spinner small /> : "Add to cast"}
        </Button>
        {!pendingPhoto && (
          <p className="faint" style={{ fontSize: "0.78rem", margin: "10px 0 0" }}>
            No photo? That&apos;s fine — Vidyra will draw them and lock the face on
            your first video, then keep it.
          </p>
        )}
      </Card>

      <h3 className="section-title" style={{ margin: "24px 0 10px" }}>
        {items.length ? `Characters (${items.length})` : "Characters"}
      </h3>

      {loading ? (
        <div style={{ display: "grid", placeItems: "center", padding: 30 }}>
          <Spinner />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon="🧑‍🤝‍🧑"
          title="No characters yet"
          hint="Add one above and they'll show up in Auto Film, Editor and Studio."
        />
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          {items.map((c) => (
            <Card key={c.character_id}>
              <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
                <div
                  style={{
                    width: 62,
                    height: 62,
                    flex: "0 0 62px",
                    borderRadius: 12,
                    overflow: "hidden",
                    background: "rgba(255,255,255,0.06)",
                    display: "grid",
                    placeItems: "center",
                  }}
                >
                  {previews[c.character_id] ? (
                    <img
                      src={previews[c.character_id]}
                      alt={c.name}
                      style={{ width: "100%", height: "100%", objectFit: "cover" }}
                    />
                  ) : (
                    <span style={{ fontSize: "1.5rem" }}>🎭</span>
                  )}
                </div>

                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 700 }}>{c.name}</div>
                  <div className="faint" style={{ fontSize: "0.8rem" }}>
                    {c.description || "No description yet."}
                  </div>
                  <div style={{ marginTop: 4 }}>
                    <span className="faint" style={{ fontSize: "0.74rem" }}>
                      {c.ref_image
                        ? "🔒 Face locked — reused in every video"
                        : "No photo yet — face locks on first video"}
                    </span>
                  </div>
                </div>

                <Button
                  variant="ghost"
                  onClick={() => void remove(c)}
                  disabled={busyId === c.character_id}
                >
                  Remove
                </Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <p className="faint" style={{ textAlign: "center", fontSize: "0.8rem", marginTop: 22 }}>
        Ready to use them?{" "}
        <Link to="/" style={{ color: "var(--accent)" }}>
          Describe a video
        </Link>{" "}
        and mention the character by name.
      </p>
    </div>
  );
}