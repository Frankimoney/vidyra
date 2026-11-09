import { Field } from "../ui";

export const DEFAULT_MUSIC_VOLUME = 0.35;

function label(volume: number) {
  if (volume <= 0) return "off";
  if (volume < 0.2) return "very low";
  if (volume < 0.45) return "low";
  if (volume < 0.7) return "medium";
  if (volume < 0.9) return "high";
  return "max";
}

function hint(volume: number) {
  if (volume <= 0) return "Music is silenced — the video will use narration only.";
  if (volume < 0.2) return "Barely there under the narration.";
  if (volume < 0.7) return "Sits under the narration without competing with it.";
  return "Clearly audible. Lower this if the voice becomes hard to follow.";
}

interface Props {
  value: number | undefined;
  onChange: (volume: number) => void;
}

/**
 * Background-music level. The bed is loudness-normalised before this is
 * applied, so the percentage means the same thing for the built-in tracks and
 * for a user upload.
 */
export default function MusicVolume({ value, onChange }: Props) {
  const volume = value ?? DEFAULT_MUSIC_VOLUME;
  return (
    <Field label={`Music volume — ${label(volume)}`}>
      <input
        type="range"
        className="slider"
        min={0}
        max={100}
        step={5}
        value={Math.round(volume * 100)}
        onChange={(e) => onChange(parseInt(e.target.value, 10) / 100)}
      />
      <p className="faint" style={{ margin: "8px 0 0", fontSize: "0.82rem" }}>
        {hint(volume)}
      </p>
    </Field>
  );
}
