================================================================================
                        VIDYRA SOFTWARE SYSTEMS (SYS DOC)
================================================================================
MODULE OBJECTIVE:  Production-harden Vidyra, the director-planning -> rendered-film web app
DOCUMENT OBJECTIVE: Capture the bug audit results, every fix shipped, and what an
                    engineer picks up next.  Workspace: backend/ (40 modules, ~9.9k LOC),
                    pipeline/ (17 modules, ~3.2k LOC), frontend/src (40 files),
                    scripts/ test harness (19 suites, ~6k LOC).
SESSION OBJECTIVES: Close every confirmed regression from the pre-session audit;
                    keep all test suites and the production build green.
PREREQUISITES:     .venv venv; ffmpeg on PATH (source bin dir of imageio_ffmpeg's
                    exe); APP_ENV=development DEV_API_KEY=vidyra-dev-key
                    PYTHONIOENCODING=utf-8 for every python run.
RUNTIME:           Windows/bash host; backend Uvicorn + Firebase or in-memory store;
                    frontend Vite + React + TS.
================================================================================

## CURRENT SESSION STATUS
- All 19 backend regression suites (scripts/test_*.py) PASS end-to-end.
- Frontend `npx tsc -b` and `npx vite build` PASS.
- Every confirmed regression from the audit is fixed and covered. The remaining
  open items below are documented limitations, not known bugs.

## KEY LESSONS LEARNED
- Server-side outlines are the single source of truth for the run-quota gate.
  The route must run the SAME estimate the worker will spend, or a route that
  under-counts lets an episode start and die with a quota error (and the worker
  is the real enforcement before anything is spent).
- Firebase docs do NOT include their doc `id` in `to_dict()`. Cleanup code that
  built a `jobId` from the doc and passed it on must read `snap.id` instead, or
  every expired job deletion silently no-ops.
- Firestore is a NoSQL store, but the code treats projects' `characters`/`scenes`
  as objects with `.voice`. `getattr(item, "voice", "")` on a bare dict silently
  yields `""` — use `.get`, and do not assume attribute access.
- Gemini is unreliable about returning "exactly one JSON object" from a chat
  call: it has returned lists, bare strings, and dicts without `sentences`. The
  fact-check pass must treat any of these as "nothing usable," never as a crash —
  best-effort means the job must still render.
- Malformed client hints (`"1080px"` for a pixel field) reach worker code as raw
  ints/strings. Convert with try/except fallbacks, not bare `int()`, and never in
  a hot path that can 500 an endpoint.
- Frontend "empty state" is not "no data": the delete-all Storyboard save must
  round-trip an empty plan, which means the backend must accept an explicitly
  cleared plan instead of re-enforcing the minimum-scene rule meant for *firming*.
- xfade batch seams are hard cuts: n clips across b batches carry n-b crossfades.
  A regression suite pins this because it is easy to re-introduce an off-by-one.

## FINISHED WORK
- Security/ownership
  - HMAC signature shape corrected on the signed render URL; share-token creation
    and verification hardened (bad tokens, flipped bits, non-ASCII, expiry,
    wrong secret all rejected). Covered by scripts/test_security.py.
  - Path traversal closed in media resolution (rejects "..", absolute, backslash
    paths). Covered by scripts/test_security.py.
  - Media ownership (`_check_media_ownership`) now enforced for ALL storage
    drivers, not just local (an attacker-controlled bucket path could copy any
    file into your uploads). Portraits clients supply are ownership-checked and
    image-kind-validated (`_validate_ref_image`).
  - Render link (`POST /films/projects/:id/render`) now requires a job that
    exists and belongs to the user; a typo could otherwise lock the project from
    editing forever (save_planning_plan refuses to edit a filmed project).
- Quota gate now single-source: `film_runs_needed`/`acting_scene_count`
  /`clone_voice_runs` in workers/film_worker.py are shared by the create_film
  guard, validate_film_package, and the worker. Podcast route now also counts
  distinct cloned host voices (worker already did). Test expectations updated
  (package 6->7, planner 5->6).
- Quota is now *visible*: `GET /studio/quota` returns `{max, used, remaining}`
  (shared `studio_service.daily_used()` helper replaced three ad-hoc copy-pasted
  counts in films.py and studio.py), surfaced as a live
  `StudioQuotaMeter` pill in the Film page (idea + podcast + package preview)
  and the Director header. Polls every 20s; refreshes after each run.
- Media library deletion: `DELETE /media/{storage_path:path}` with
  `_owned_media_path` ownership guard (own media/studio/videos prefixes only,
  rejects `..`, backslash, empty segments, foreign users); both storage drivers
  already had `delete_file`. AssetPicker now shows a per-tile 🗑 with a
  confirm dialog; covered by test_security.py and a live-API smoke test.
- AI Studio prompt fields are per-tool: `Studio.tsx` owns a `PROMPT_COPY` map
  (label + placeholder for all 9 tools, incl. txt2img's "Describe the image you
  want to create…") and renders it first; the backend catalog also ships
  `promptLabel`/`promptHint` as fallback for API clients. The UI copy is
  authoritative so a stale backend never reverts the placeholders.
- Job/lifecycle cleanup
  - Cleanup injects firebase doc `id` as `jobId`; deletes the job record before
    files; refuses empty jobId. Files are removed only while a record matches,
    so a crash cannot strand a half-deleted job.
  - Orphaned-media cleanup now tracks clone voices (`clone:<path>`) from
    settings/scenes/characters, character-library refImages/voices, and
    project-cast portraits; previously each sweep deleted referenced clones.
- Storyboard save-back
  - Frontend now preserves `show_page_name` (and pacing/transition/music/
    captions/hook/sources) across saves; verified by backend round-trip test.
  - Supports delete-all: an explicitly empty plan clears the storyboard and
    retires the row (backend `save_planning_plan` accepts cleared plans now).
  - Per-project save rows (was only the first source project), duplicate-import
    guard, `placedAt`/sourceIndex re-basing after save, `<2 scenes`/`!mine`
    eligibility bug fixed. `tsc -b` + `vite build` green.
- Frontend UX/data bugs: Profile page-name latch + empty-save guard; Create
  autosave gating (`autosaveReady`); VideoCard `done`/`playable` from live job
  output; JobStatus retry passes `scenes`+`characters` (retrying used to
  regenerate a different script).
- Numeric settings sanitized centrally: `bounded_int`/`bounded_float` (out-of-
  range -> safe fallback, never clamp), `_even` for dimensions, applied in
  `RenderSettings.__post_init__`; `words_per_line`, fps, speech_rate,
  hook_duration, font_size, music_volume bounds enforced; face overlay
  recordingWidth/recordingHeight and scale hints parse safely. Bad dims now
  fall back to 1080x1920 instead of crashing renders.
- Async event-loop blocking removed: uploads (ffmpeg split, store, probe) and
  planning/compile LLM turns run via run_in_threadpool.
- pipeline/transitions.py: Auto-transition off-by-one fixed; new
  scripts/test_transitions.py pins it.
- fact-check pass hardened against malformed Gemini shapes (returns
  "unexpected shape" instead of an unhandled worker crash) and drops malformed
  source entries.
- backend/app/config.py: default gemini model gemini-2.5-flash -> gemini-3.1-
  flash-lite (2.5 is now 404 for new accounts); launcher studio.env still wins.
- New suites: scripts/test_transitions.py, scripts/test_security.py. Updated:
  scripts/test_planner.py, test_storyboard.py, test_package.py.

## NEXT SESSION - OBJECTIVES
1. Flex the new quota gate on a fresh studio account (no prior runs) to confirm
   the "idea-only lower bound" and filmed-project exact counts feel right; tune
   the UIs run-estimate strings (planner/film/orchestrator) if they drift from
   `film_runs_needed`.
2. Drive an end-to-end podcast episode with clone voices through the dev server
   and confirm the run count estimate matches the worker spend.
3. Optional production follow-ups from the audit that were consciously skipped
   (see OPEN ITEMS).

## KEY DECISIONS
- Quota estimate is exact for pasted production-package plans; the idea-only
  creator path uses a documented lower bound and the worker re-checks before
  spending (never lets a ~quota-busted render start).
- `clean_orphaned_media` runs ONLY on the local storage driver; extending it to
  Firebase was judged not cost-effective (sync of clone/portrait refs is the
  actual fix). Keep it local.
- Podcast cast quota `max(2, len(hosts)) + n` plus clone-host runs kept as-is;
  route estimate now over-counts if the agent never gives a host a line, but the
  worker re-checks the exact number before spending anything.
- Batch seams in xfade stay as hard cuts by design; not a bug.
- Gemini "unexpected shape" in fact-check means leave text as written, report
  the error, film anyway.

## OPEN ITEMS / RISKS
- `projects.append_messages` is a read-modify-write on the project doc. In
  practice the app is a single writer per project (one user, sequential turns),
  so a lock was not added; if the planning director ever runs concurrent
  writers, convert this to a Firestore transaction/arrayUnion.
- Podcast quota uses host voices as an upper bound until the agent writes turns;
  a host that never speaks over-counts at the very end of the day. Cosmetic.
- "Reshoot with same inputs" still reuses the most recent satisfied-history task
  (compaction guard); always confirm the final output is the fresh render.
- Gemini model choice: if gemini-3.1-flash-lite names change, update the default
  in backend/app/config.py and in studio.env.