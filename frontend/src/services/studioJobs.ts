import {
  doc,
  onSnapshot,
  query,
  collection,
  where,
  orderBy,
  limit,
} from "firebase/firestore";
import { db } from "../firebase";
import type { StudioRun } from "../types";

/** Realtime subscription to one studio run. Returns an unsubscribe fn. */
export function watchStudioRun(
  runId: string,
  onDoc: (run: StudioRun) => void
): () => void {
  return onSnapshot(doc(db, "studio_runs", runId), (snap) => {
    if (snap.exists()) {
      onDoc({ ...(snap.data() as StudioRun), run_id: snap.id });
    }
  });
}

/** Realtime subscription to the current user's studio runs (newest first). */
export function watchStudioRuns(
  uid: string,
  onList: (runs: StudioRun[]) => void,
  max = 40
): () => void {
  const q = query(
    collection(db, "studio_runs"),
    where("userId", "==", uid),
    orderBy("createdAt", "desc"),
    limit(max)
  );
  return onSnapshot(q, (snap) => {
    const runs = snap.docs.map((d) => ({ ...(d.data() as StudioRun), run_id: d.id }));
    onList(runs);
  });
}