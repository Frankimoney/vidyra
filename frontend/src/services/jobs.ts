import {
  doc,
  onSnapshot,
  query,
  collection,
  where,
  orderBy,
  limit,
  startAfter,
} from "firebase/firestore";
import type { QueryConstraint, QueryDocumentSnapshot } from "firebase/firestore";
import { db } from "../firebase";
import type { JobItem } from "../types";

/** Realtime subscription to one job document. Returns an unsubscribe fn. */
export function watchJob(
  jobId: string,
  onDoc: (job: JobItem) => void
): () => void {
  return onSnapshot(doc(db, "videos", jobId), (snap) => {
    if (snap.exists()) {
      onDoc({ ...(snap.data() as JobItem), jobId: snap.id });
    }
  });
}

/**
 * One page of the user's videos, newest first.
 *
 * Pass the last document of the previous page as `cursor` to get the page after
 * it; pass null for the first page. The `videos` composite index in
 * firestore.indexes.json already covers userId + createdAt, so both the first
 * page and the `startAfter` continuation resolve on the same index.
 */
export function watchJobsPage(
  uid: string,
  pageSize: number,
  cursor: QueryDocumentSnapshot | null,
  onPage: (jobs: JobItem[], last: QueryDocumentSnapshot | null) => void
): () => void {
  const constraints: QueryConstraint[] = [
    where("userId", "==", uid),
    orderBy("createdAt", "desc"),
    limit(pageSize),
  ];
  // startAfter must sit between the filters and the limit.
  if (cursor) constraints.splice(2, 0, startAfter(cursor));

  const q = query(collection(db, "videos"), ...constraints);
  return onSnapshot(q, (snap) => {
    const jobs = snap.docs.map((d) => ({ ...(d.data() as JobItem), jobId: d.id }));
    const last = snap.docs.length ? snap.docs[snap.docs.length - 1] : null;
    onPage(jobs, last);
  });
}

/** Realtime subscription to the current user's videos (newest first). */
export function watchJobs(
  uid: string,
  onList: (jobs: JobItem[]) => void,
  max = 40
): () => void {
  const q = query(
    collection(db, "videos"),
    where("userId", "==", uid),
    orderBy("createdAt", "desc"),
    limit(max)
  );
  return onSnapshot(q, (snap) => {
    const jobs = snap.docs.map((d) => ({ ...(d.data() as JobItem), jobId: d.id }));
    onList(jobs);
  });
}

/**
 * Human label for how many scenes a film has and which one is filming now.
 *
 * Total is read from the written outline first (most accurate), then the
 * requested count on the film, then the worker-reported count. The current
 * index only exists once the worker starts filming each scene. Returns null
 * for non-film jobs (storyboard/face) so callers can skip the chip.
 */
export function sceneProgress(job: JobItem): {
  index: number | null;
  total: number | null;
  label: string | null;
} {
  const outlineScenes = job.filmPlot?.scenes;
  const fromOutline =
    Array.isArray(outlineScenes) && outlineScenes.length ? outlineScenes.length : 0;
  const total =
    fromOutline ||
    (job.film?.outline?.scenes?.length ?? 0) ||
    (job.film?.scenes_count ?? 0) ||
    (job.sceneCount ?? 0);
  const index = job.sceneIndex && job.sceneIndex > 0 ? job.sceneIndex : null;
  if (!total) return { index, total: null, label: null };
  if (total <= 1) return { index, total, label: "Single scene" };
  if (index) return { index, total, label: `Scene ${index} of ${total}` };
  return { index, total, label: `${total} scenes` };
}