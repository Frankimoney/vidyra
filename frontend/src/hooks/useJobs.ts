import { useCallback, useEffect, useRef, useState } from "react";
import type { QueryDocumentSnapshot } from "firebase/firestore";
import { useAuth } from "../contexts/AuthContext";
import { watchJobs, watchJobsPage, watchJob } from "../services/jobs";
import type { JobItem } from "../types";

/** All jobs for the current user (realtime). */
export function useJobs() {
  const { uid } = useAuth();
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!uid) return;
    const unsub = watchJobs(uid, (list) => setJobs(list));
    setLoading(false);
    return unsub;
  }, [uid]);

  return { jobs, loading };
}

/**
 * The user's videos a page at a time, so a long library stays reachable
 * instead of being cut off at the realtime cap. The first page is a live
 * subscription; each "load more" appends one page from the last document seen.
 */
export function useJobsPage(pageSize = 24) {
  const { uid } = useAuth();
  const [jobs, setJobs] = useState<JobItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [done, setDone] = useState(false);

  const cursorRef = useRef<QueryDocumentSnapshot | null>(null);
  // Guards against a stale loadMore firing after uid/pageSize changed.
  const liveRef = useRef(true);

  useEffect(() => {
    if (!uid) return;
    liveRef.current = true;
    cursorRef.current = null;
    setJobs([]);
    setDone(false);
    setLoading(true);

    const unsub = watchJobsPage(uid, pageSize, null, (page, last) => {
      if (!liveRef.current) return;
      cursorRef.current = last;
      setJobs(page);
      // A short page means Firestore had nothing left to hand back.
      setDone(page.length < pageSize);
      setLoading(false);
    });

    return () => {
      liveRef.current = false;
      unsub();
    };
  }, [uid, pageSize]);

  const loadMore = useCallback(() => {
    if (!uid || !cursorRef.current || loadingMore || done) return;
    setLoadingMore(true);

    // This page is a one-shot read: take the first snapshot, append it, detach.
    const stop = watchJobsPage(uid, pageSize, cursorRef.current, (page, last) => {
      if (!liveRef.current) return;
      if (page.length) {
        cursorRef.current = last;
        setJobs((prev) => {
          // A video created between pages can shift the window and repeat a row.
          const seen = new Set(prev.map((j) => j.jobId));
          return [...prev, ...page.filter((j) => !seen.has(j.jobId))];
        });
      }
      if (page.length < pageSize) setDone(true);
      setLoadingMore(false);
      stop();
    });
  }, [uid, pageSize, loadingMore, done]);

  return { jobs, loading, loadingMore, done, loadMore };
}

/** A single job with realtime updates. */
export function useJob(jobId: string) {
  const [job, setJob] = useState<JobItem | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!jobId) return;
    const unsub = watchJob(jobId, (doc) => {
      setJob(doc);
      setLoading(false);
    });
    return unsub;
  }, [jobId]);

  return { job, loading };
}