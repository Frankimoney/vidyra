import type { CloneVoiceOption } from "../hooks/useCloneVoices";

interface Props {
  value: string;
  onChange: (value: string) => void;
  voices: string[];
  cloneVoices?: CloneVoiceOption[];
  /** Value meaning "inherit the default voice" (per-scene pickers). */
  inheritLabel?: string;
  inheritValue?: string;
  id?: string;
}

/**
 * The one voice picker: built-in Piper voices plus cloned voices from AI
 * Studio, in the same <select>. Used by Create, Edit, Storyboard and Film so a
 * cloned voice can never become a second, separate control.
 */
export default function VoiceSelect({
  value,
  onChange,
  voices,
  cloneVoices = [],
  inheritLabel,
  inheritValue = "",
  id,
}: Props) {
  return (
    <select className="input" id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      {inheritLabel !== undefined && (
        <option value={inheritValue}>{inheritLabel}</option>
      )}
      <optgroup label="Built-in voices">
        {voices.map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </optgroup>
      {cloneVoices.length > 0 && (
        <optgroup label="Cloned voices (AI Studio)">
          {cloneVoices.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </optgroup>
      )}
    </select>
  );
}
