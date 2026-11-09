import { useMemo, useRef, useState } from "react";
import { Button, Field, Segmented, Toggle } from "../ui";
import { api, ApiError } from "../../services/api";
import type { VideoSettings } from "../../types";
import AspectSelect from "../AspectSelect";
import MusicVolume from "./MusicVolume";

interface Props {
  settings: VideoSettings;
  onUpdate: (patch: Partial<VideoSettings>) => void;
  captionStyles: string[];
  captionPositions: string[];
  transitions: string[];
  music: string[];
  pageName: string;
}

export default function StepVisuals({
  settings,
  onUpdate,
  captionStyles,
  captionPositions,
  transitions,
  music,
  pageName,
}: Props) {
  const showPage = settings.show_page_name ?? true;
  const fileRef = useRef<HTMLInputElement>(null);
  const [extraMusic, setExtraMusic] = useState<string[]>([]);
  const [uploadingMusic, setUploadingMusic] = useState(false);
  const [musicError, setMusicError] = useState("");

  const allMusic = useMemo(() => {
    const seen = new Set<string>();
    return [...music, ...extraMusic].filter((m) => {
      if (seen.has(m)) return false;
      seen.add(m);
      return true;
    });
  }, [music, extraMusic]);

  function musicLabel(value: string) {
    if (!value.startsWith("media/")) return value;
    const base = (value.split("/").pop() ?? value).replace(/\.[^.]+$/, "");
    return `${base} (mine)`;
  }

  async function handleMusicFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploadingMusic(true);
    setMusicError("");
    try {
      const res = await api.uploadMusic(file);
      setExtraMusic((prev) => [...prev, res.storagePath]);
      onUpdate({ music: res.storagePath });
    } catch (err) {
      setMusicError(err instanceof ApiError ? err.message : "Could not upload the music file.");
    } finally {
      setUploadingMusic(false);
    }
  }

  return (
    <div className="stack-v fade-up">
      <Toggle
        label="Captions"
        on={settings.captions}
        onChange={(v) => onUpdate({ captions: v })}
      />

      {settings.captions && (
        <>
          <Field label="Caption style">
            <Segmented
              options={captionStyles as [string, ...string[]]}
              value={settings.caption_style}
              onChange={(v) => onUpdate({ caption_style: v })}
            />
          </Field>

          <Field label={`Font size — ${settings.font_size}`}>
            <input
              type="range"
              className="slider"
              min={36}
              max={90}
              step={2}
              value={settings.font_size}
              onChange={(e) => onUpdate({ font_size: parseInt(e.target.value, 10) })}
            />
          </Field>

          <Field label={`Words per line — ${settings.words_per_line}`}>
            <input
              type="range"
              className="slider"
              min={2}
              max={8}
              step={1}
              value={settings.words_per_line}
              onChange={(e) => onUpdate({ words_per_line: parseInt(e.target.value, 10) })}
            />
          </Field>

          <Field label="Caption position">
            <Segmented
              options={captionPositions as [string, ...string[]]}
              value={settings.caption_position}
              onChange={(v) => onUpdate({ caption_position: v })}
            />
          </Field>
        </>
      )}

      <div className="divider" />

      <Toggle
        label="Show my page name"
        on={showPage}
        onChange={(v) => onUpdate({ show_page_name: v })}
      />

      {showPage && (
        <Field label="Page name (top-right badge)">
          <input
            className="input"
            type="text"
            placeholder={pageName ? `@${pageName}` : "@YourHandle"}
            value={settings.page_name ?? ""}
            maxLength={30}
            onChange={(e) => onUpdate({ page_name: e.target.value })}
          />
          <p className="faint" style={{ margin: "8px 0 0", fontSize: "0.82rem" }}>
            Leave blank to use your profile page name{pageName ? ` (@${pageName})` : ""}.
          </p>
        </Field>
      )}

      <Field label="Aspect ratio">
        <AspectSelect
          id="create-aspect"
          value={settings.aspect ?? "auto"}
          onChange={(v) => onUpdate({ aspect: v })}
          hint="Drives every generated image and the final video. Auto keeps the vertical default."
        />
      </Field>

      <Field label="Reframe">
        <Segmented
          options={["auto", "crop", "blur"] as [string, ...string[]]}
          value={settings.reframe ?? "auto"}
          onChange={(v) => onUpdate({ reframe: v })}
        />
        <p className="faint" style={{ margin: "8px 0 0", fontSize: "0.82rem" }}>
          {settings.reframe === "auto" &&
            "Auto: fills the frame when media is close to the chosen ratio, otherwise keeps it whole with a blurred background."}
          {settings.reframe === "crop" && "Full-bleed: crops media that does not match the chosen ratio (sides may be cut off)."}
          {settings.reframe === "blur" && "Keeps the whole frame visible with a blurred background filling the rest."}
        </p>
      </Field>

      <Field label="Transition style">
        <Segmented
          options={transitions as [string, ...string[]]}
          value={settings.transition}
          onChange={(v) => onUpdate({ transition: v })}
        />
        <p className="faint" style={{ margin: "8px 0 0", fontSize: "0.82rem" }}>
          {settings.transition === "Auto" && "Auto picks the best transition per cut."}
          {settings.transition === "fade" && "Soft crossfade between scenes."}
          {settings.transition === "slideleft" && "Scenes slide left."}
          {settings.transition === "slideright" && "Scenes slide right."}
          {settings.transition === "wipeleft" && "New scene wipes in from the right."}
          {settings.transition === "wiperight" && "New scene wipes in from the left."}
        </p>
      </Field>

      <Field label="Background music">
        <select
          className="input"
          value={settings.music ?? ""}
          onChange={(e) => onUpdate({ music: e.target.value || null })}
        >
          <option value="">No music</option>
          {allMusic.map((m) => (
            <option key={m} value={m}>
              {musicLabel(m)}
            </option>
          ))}
        </select>
        <input
          ref={fileRef}
          type="file"
          accept="audio/*"
          hidden
          onChange={handleMusicFile}
        />
        <Button
          variant="ghost"
          loading={uploadingMusic}
          disabled={uploadingMusic}
          onClick={() => fileRef.current?.click()}
        >
          {uploadingMusic ? "Uploading…" : "Upload my own music"}
        </Button>
        {musicError && (
          <p className="faint" style={{ margin: "8px 0 0", color: "#e5484d", fontSize: "0.82rem" }}>
            {musicError}
          </p>
        )}
      </Field>

      {settings.music && (
        <MusicVolume
          value={settings.music_volume}
          onChange={(music_volume) => onUpdate({ music_volume })}
        />
      )}
    </div>
  );
}