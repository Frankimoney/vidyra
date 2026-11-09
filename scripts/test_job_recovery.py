"""Tests for boot-time recovery of jobs stranded by a crash or power cut.

The backend is the only thing driving a render, so when the process dies any
document still marked queued/processing has no worker behind it. These tests pin
that such documents are reconciled to failed on the next boot, and that
completed, failed, and in-progress work are left alone.

Firestore is faked at the collection level (see _FakeCollection) so the recovery
path is exercised without credentials, the same way the rest of the local suites
avoid cloud dependencies.

Run:  PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/test_job_recovery.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.services import cleanup  # noqa: E402
from backend.app.services import jobs as jobs_service  # noqa: E402
from backend.app.services import studio as studio_service  # noqa: E402

FAILED = []


def check(name, condition, detail=""):
    if condition:
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILED.append(name)
        print(f"  FAIL  {name}" + (f" — {detail}" if detail else ""))


class _FakeDoc:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data

    def to_dict(self):
        return dict(self._data)

    def update(self, fields):
        self._data.update(fields)


class _FakeCollection:
    def __init__(self, docs):
        self._docs = {d_id: _FakeDoc(d_id, data) for d_id, data in docs.items()}

    def document(self, doc_id):
        return self._docs[doc_id]

    def get(self):
        return list(self._docs.values())


class _FakeDb:
    def __init__(self, jobs, runs):
        self._collections = {
            jobs_service.COLLECTION: _FakeCollection(jobs),
            studio_service.COLLECTION: _FakeCollection(runs),
        }

    def collection(self, name):
        return self._collections[name]

    def raw(self, name):
        return self._collections[name]._docs


def _install(monkey_jobs, monkey_runs):
    """Point both services' update helpers and firebase at the fake db."""
    fake = _FakeDb(monkey_jobs, monkey_runs)
    cleanup.firebase.db = fake

    jobs_service._using_dev_store = lambda: False
    jobs_service._firestore_doc = lambda job_id: fake.collection(
        jobs_service.COLLECTION
    ).document(job_id)

    studio_service._db = lambda: fake
    studio_service.update_run = lambda run_id, **fields: (
        fields.update(updatedAt="now"),
        fake.collection(studio_service.COLLECTION)
        .document(run_id)
        .update(fields),
    )
    return fake


def _restore():
    cleanup.firebase.db = None


def test_interrupted_marked_failed():
    print("interrupted jobs and runs:")
    jobs = {
        "j_proc": {"userId": "u", "status": "processing", "stage": "rendering",
                   "progress": 42, "message": "Rendering", "error": None},
        "j_queued": {"userId": "u", "status": "queued", "stage": "queued",
                     "progress": 0, "message": "Queued", "error": None},
    }
    runs = {
        "r_proc": {"userId": "u", "status": "processing", "stage": "generating",
                   "progress": 10, "message": "Generating", "error": None},
    }
    fake = _install(jobs, runs)

    changed = cleanup.fail_interrupted_jobs()
    check("reports how many documents changed", changed == 3, str(changed))

    for name, doc in (("job", fake.raw(jobs_service.COLLECTION)["j_proc"].to_dict()),
                      ("queued job", fake.raw(jobs_service.COLLECTION)["j_queued"].to_dict()),
                      ("studio run", fake.raw(studio_service.COLLECTION)["r_proc"].to_dict())):
        check(f"{name} is marked failed", doc["status"] == "failed", doc["status"])
        check(f"{name} stage is failed", doc["stage"] == "failed", doc["stage"])
        check(f"{name} explains the interruption",
              "Interrupted" in (doc.get("error") or ""), (doc.get("error") or "")[:40])
        check(f"{name} has an error for the UI", bool(doc.get("error")))

    proc = fake.raw(jobs_service.COLLECTION)["j_proc"].to_dict()
    check("progress reached before the crash is kept", proc["progress"] == 42,
          str(proc["progress"]))


def test_finished_untouched():
    print("finished and failed work:")
    jobs = {
        "done": {"userId": "u", "status": "completed", "stage": "completed",
                 "progress": 100, "message": "Done"},
        "bad": {"userId": "u", "status": "failed", "stage": "failed",
                "progress": 30, "message": "ffmpeg blew up", "error": "boom"},
    }
    fake = _install(jobs, {})

    changed = cleanup.fail_interrupted_jobs()
    check("nothing to reconcile", changed == 0, str(changed))
    check("completed job untouched",
          fake.raw(jobs_service.COLLECTION)["done"].to_dict()["message"] == "Done")
    check("failed job keeps its real error",
          fake.raw(jobs_service.COLLECTION)["bad"].to_dict()["error"] == "boom")


def test_realistic_crash_document():
    print("a realistic stranded document:")
    jobs = {
        "job1": {
            "userId": "u1", "type": "storyboard", "title": "Match night",
            "status": "processing", "stage": "rendering", "progress": 65,
            "message": "Rendering 3/5", "error": None,
            "script": "It went to extra time.", "media": ["media/u1/clip.mp4"],
            "output": None,
        }
    }
    fake = _install(jobs, {})
    cleanup.fail_interrupted_jobs()
    doc = fake.raw(jobs_service.COLLECTION)["job1"]

    check("status is honest", doc.to_dict()["status"] == "failed")
    check("script is preserved for a retry",
          doc.to_dict()["script"] == "It went to extra time.")
    check("uploads are preserved", doc.to_dict()["media"] == ["media/u1/clip.mp4"])
    check("no output was invented", doc.to_dict()["output"] is None)
    check("message does not blame the user's content",
          "ffmpeg" not in (doc.to_dict().get("error") or ""))


def test_dev_store_noop():
    print("dev mode:")
    _restore()
    changed = cleanup.fail_interrupted_jobs()
    check("no firestore means nothing to reconcile", changed == 0, str(changed))


def main():
    try:
        test_interrupted_marked_failed()
        test_finished_untouched()
        test_realistic_crash_document()
        test_dev_store_noop()
    finally:
        _restore()
    print()
    if FAILED:
        print(f"FAILED: {len(FAILED)} -> {FAILED}")
        return 1
    print("ALL JOB RECOVERY TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
