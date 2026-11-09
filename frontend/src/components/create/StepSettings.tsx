import { Field, Segmented, Toggle } from "../ui";
import VoiceSelect from "../VoiceSelect";
import type { CloneVoiceOption } from "../../hooks/useCloneVoices";
import type { VideoSettings } from "../../types";

interface Props {
  settings: VideoSettings;
  onUpdate: (patch: Partial<VideoSettings>) => void;
  voices: string[];
  pacing: string[];
  hooks: string[];
  minRate: number;
  maxRate: number;
  /** Cloned voices ("clone:<path>") from Studio/library — same dropdown, no second picker. */
  cloneVoices?: CloneVoiceOption[];
}

export default function StepSettings({
  settings,
  onUpdate,
  voices,
  pacing,
  hooks,
  minRate,
  maxRate,
  cloneVoices = [],
}: Props) {
  return (
    <div className="stack-v fade-up">
      <Field label="Narrator voice">
        <VoiceSelect
          value={settings.voice}
          onChange={(voice) => onUpdate({ voice })}
          voices={voices}
          cloneVoices={cloneVoices}
        />
        {settings.voice.startsWith("clone:") && (
          <p className="faint" style={{ margin: "8px 0 0", fontSize: "0.82rem" }}>
            Cloned narration uses 1 GPU run (up to 2000 chars). Speech-rate still applies.
          </p>
        )}
      </Field>

      <Field label={`Narration speed — ${settings.speech_rate.toFixed(2)}x`}>
        <input
          type="range"
          className="slider"
          min={minRate}
          max={maxRate}
          step={0.01}
          value={settings.speech_rate}
          onChange={(e) => onUpdate({ speech_rate: parseFloat(e.target.value) })}
        />
      </Field>

      <Field label="Sports pacing">
        <Segmented
          options={pacing as [string, ...string[]]}
          value={settings.pacing}
          onChange={(v) => onUpdate({ pacing: v })}
        />
        <p className="faint" style={{ margin: "8px 0 0", fontSize: "0.82rem" }}>
          {settings.pacing === "Breaking News" && "Fast cuts, snappy delivery."}
          {settings.pacing === "Normal Sports Story" && "Balanced, natural rhythm."}
          {settings.pacing === "High Energy" && "Rapid-fire energy for highlights."}
          {settings.pacing === "Analysis" && "Slower, measured storytelling."}
        </p>
      </Field>

      <div className="divider" />

      <Toggle
        label="Opening hook"
        on={settings.hook}
        onChange={(v) => onUpdate({ hook: v })}
      />

      {settings.hook && (
        <>
          <Field label="Hook style">
            <Segmented
              options={hooks as [string, ...string[]]}
              value={settings.hook_style}
              onChange={(v) => onUpdate({ hook_style: v })}
            />
          </Field>

          <div className="grid-2">
            <Field label="Hook text">
              <input
                className="input"
                value={settings.hook_text}
                onChange={(e) => onUpdate({ hook_text: e.target.value })}
                placeholder="e.g. BROOKE QUARLES DANIELS"
              />
            </Field>
            <Field label="Hook subtext">
              <input
                className="input"
                value={settings.hook_subtext}
                onChange={(e) => onUpdate({ hook_subtext: e.target.value })}
                placeholder="e.g. IS PUTTING IN THE WORK"
              />
            </Field>
          </div>

          <Field label={`Hook duration — ${settings.hook_duration.toFixed(1)}s`}>
            <input
              type="range"
              className="slider"
              min={0.5}
              max={3}
              step={0.1}
              value={settings.hook_duration}
              onChange={(e) => onUpdate({ hook_duration: parseFloat(e.target.value) })}
            />
          </Field>
        </>
      )}
    </div>
  );
}