import type { VideoSettings } from "../types";

export const DEFAULT_SETTINGS: VideoSettings = {
  voice: "",
  speech_rate: 1.0,
  pacing: "Normal Sports Story",
  hook: true,
  hook_style: "Breaking News",
  hook_text: "",
  hook_subtext: "",
  hook_duration: 1.5,
  captions: import.meta.env.VITE_DEFAULT_CAPTIONS !== "off",
  caption_style: "Karaoke",
  font_size: 58,
  words_per_line: 5,
  caption_position: "Bottom",
  music: null,
  music_volume: 0.35,
  transition: "Auto",
  reframe: "auto",
  show_page_name: true,
  page_name: "",
};

/** Merge a job/workspace `settings` blob onto the app defaults. */
export function settingsFrom(raw: Record<string, unknown>): VideoSettings {
  const s = raw as Record<string, any>;
  return {
    voice: (s.voice as string) || "",
    speech_rate: Number(s.speech_rate ?? DEFAULT_SETTINGS.speech_rate),
    pacing: (s.pacing as string) || DEFAULT_SETTINGS.pacing,
    hook: s.hook !== false,
    hook_style: (s.hook_style as string) || DEFAULT_SETTINGS.hook_style,
    hook_text: (s.hook_text as string) || "",
    hook_subtext: (s.hook_subtext as string) || "",
    hook_duration: Number(s.hook_duration ?? DEFAULT_SETTINGS.hook_duration),
    captions: s.captions !== false,
    caption_style: (s.caption_style as string) || DEFAULT_SETTINGS.caption_style,
    font_size: Number(s.font_size ?? DEFAULT_SETTINGS.font_size),
    words_per_line: Number(s.words_per_line ?? DEFAULT_SETTINGS.words_per_line),
    caption_position: (s.caption_position as string) || DEFAULT_SETTINGS.caption_position,
    music: (s.music as string) || null,
    music_volume: Number(s.music_volume ?? DEFAULT_SETTINGS.music_volume),
    transition: (s.transition as string) || DEFAULT_SETTINGS.transition,
    reframe: (s.reframe as string) || DEFAULT_SETTINGS.reframe,
    show_page_name: s.show_page_name !== false,
    aspect: (s.aspect as string) || undefined,
    page_name: (s.page_name as string) || "",
  };
}
