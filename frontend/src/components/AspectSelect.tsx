/**
 * The one aspect-ratio picker.
 *
 * Used by AI Studio, Create/Edit, Storyboard and Auto Film so the output shape
 * is never a second, divergent control. The list mirrors `pipeline.aspect`
 * (ASPECT_CHOICES / ASPECT_LABELS); the backend is the source of truth and
 * rejects anything it does not recognise.
 *
 * "auto" means "keep whatever this surface already defaults to" — the model's
 * tuned canvas in Studio, the historical 9:16 canvas in the video pipeline —
 * so existing renders are unchanged until someone picks a shape.
 */
export interface AspectChoice {
  value: string;
  label: string;
}

/** Fallback list, used before the catalog has loaded. Mirrors pipeline.aspect. */
export const FALLBACK_ASPECTS: AspectChoice[] = [
  { value: "auto", label: "Auto (default shape)" },
  { value: "9:16", label: "9:16 — vertical (Reels, Shorts, TikTok)" },
  { value: "16:9", label: "16:9 — widescreen (YouTube)" },
  { value: "1:1", label: "1:1 — square (feed post)" },
  { value: "4:5", label: "4:5 — portrait (feed post)" },
  { value: "5:4", label: "5:4 — landscape" },
  { value: "3:4", label: "3:4 — portrait" },
  { value: "4:3", label: "4:3 — classic TV" },
  { value: "3:2", label: "3:2 — landscape" },
  { value: "2:3", label: "2:3 — portrait" },
  { value: "21:9", label: "21:9 — ultrawide (cinematic)" },
];

interface Props {
  value: string;
  onChange: (value: string) => void;
  /** Choice list from the backend catalog, when available. */
  choices?: AspectChoice[];
  id?: string;
  label?: string;
  hint?: string;
  disabled?: boolean;
}

/** Build choices from the catalog's `paramOptions`, falling back to the list above. */
export function aspectsFromCatalog(paramOptions?: {
  aspect?: string[];
  aspectLabels?: Record<string, string>;
}): AspectChoice[] {
  const values = paramOptions?.aspect;
  const labels = paramOptions?.aspectLabels;
  if (!values || values.length === 0) return FALLBACK_ASPECTS;
  return values.map((v) => ({ value: v, label: labels?.[v] ?? v }));
}

export default function AspectSelect({
  value,
  onChange,
  choices,
  id,
  label,
  hint,
  disabled,
}: Props) {
  const options = choices && choices.length > 0 ? choices : FALLBACK_ASPECTS;
  return (
    <>
      {label && (
        <label className="field-label" htmlFor={id}>
          {label}
        </label>
      )}
      <select
        className="input"
        id={id}
        value={value || "auto"}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      {hint && (
        <p className="faint" style={{ fontSize: "0.78rem", margin: "4px 0 0" }}>
          {hint}
        </p>
      )}
    </>
  );
}