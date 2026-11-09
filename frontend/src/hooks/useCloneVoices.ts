import { useEffect, useState } from "react";
import { api } from "../services/api";

export interface CloneVoiceOption {
  label: string;
  value: string;
}

/**
 * Cloned voices the user has already made in AI Studio, as
 * "clone:<storagePath>" values — the same shape the backend resolves.
 * Asks the library for audio tagged as a voice sample, so uploaded music
 * tracks are not offered here as cloneable voices.
 */
export function useCloneVoices(): CloneVoiceOption[] {
  const [clones, setClones] = useState<CloneVoiceOption[]>([]);

  useEffect(() => {
    let alive = true;
    api
      .library("audio", "voice")
      .then((lib) => {
        if (!alive) return;
        setClones(lib.items.map((a) => ({ label: `🎙 ${a.name}`, value: `clone:${a.path}` })));
      })
      .catch(() => {
        /* library unavailable — built-in voices only */
      });
    return () => {
      alive = false;
    };
  }, []);

  return clones;
}
