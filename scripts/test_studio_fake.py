"""In-process end-to-end test of the AI Studio fake-GPU flow.

Run:  RUNPOD_FAKE=1 STORAGE_ROOT=storage/teststudio DEV_API_KEY=vidyra-dev-key \
        PYTHONPATH=backend .venv/Scripts/python.exe scripts/test_studio_fake.py
"""

import os
import sys
import time

os.environ.setdefault("RUNPOD_FAKE", "1")
os.environ.setdefault("STORAGE_ROOT", "storage/teststudio")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DEV_API_KEY", "vidyra-dev-key")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from starlette.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

KEY = "vidyra-dev-key"
H = {"Authorization": f"Bearer {KEY}"}


def main() -> int:
    failures = []

    def check(name, cond, extra=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{(' — ' + extra) if extra else ''}")
        if not cond:
            failures.append(name)

    with TestClient(app) as client:
        print("catalog:")
        r = client.get("/api/catalog/studio", headers=H)
        check("catalog 200", r.status_code == 200, str(r.status_code))
        cat = r.json()
        check("studio enabled (fake)", cat.get("enabled") and cat.get("fake"))
        check("6 tools", len(cat.get("tools", [])) == 6, str(len(cat.get("tools", []))))
        check("img2img tool listed",
              any(t.get("tool") == "img2img" for t in cat.get("tools", [])))
        check("lipsync tool listed",
              any(t.get("tool") == "lipsync" for t in cat.get("tools", [])))
        check("portrait-lock character tool listed",
              any(t.get("tool") == "character" for t in cat.get("tools", [])))
        check("voice clone tool listed",
              any(t.get("tool") == "voiceclone" for t in cat.get("tools", [])))

        print("catalog only exposes controls the model honors:")
        params = cat.get("modelParams", {})
        lipsync = params.get("lipsync", {}).get("wav2lip", {})
        check("Wav2Lip has no diffusion/prompt controls", lipsync == {}, str(lipsync))
        voice = params.get("voiceclone", {}).get("chatterbox-turbo", {})
        check("Chatterbox has no seed/negative controls",
              "seed" not in voice and "negative" not in voice, str(voice))
        camera = params.get("img2video", {}).get("wan22-cam-i2v", {})
        check("camera model exposes trajectory controls",
              "camera_pose" in camera and "camera_speed" in camera, str(camera))
        check("camera model hides unused denoise", "denoise" not in camera, str(camera))
        check("catalog publishes camera pose choices",
              "Zoom In" in cat.get("paramOptions", {}).get("camera_pose", []),
              str(cat.get("paramOptions", {}).get("camera_pose")))

        print("create txt2img run:")
        r = client.post(
            "/api/studio/runs",
            headers=H,
            json={
                "tool": "txt2img",
                "model": "sdxl",
                "prompt": "a dramatic soccer goal at golden hour",
                "params": {"width": 384, "height": 384, "steps": 12},
                "refs": [],
            },
        )
        check("create 201", r.status_code == 201, r.text[:200])
        run_id = r.json().get("run_id")
        check("run_id returned", bool(run_id))

        print("poll run:")
        status = None
        for _ in range(30):
            time.sleep(1)
            r = client.get(f"/api/studio/runs/{run_id}", headers=H)
            status = r.json()
            if status.get("status") in ("completed", "failed"):
                break
        check("run_id echoed on GET", status.get("run_id") == run_id, str(status.get("run_id")))
        check("status completed", status.get("status") == "completed", str(status.get("error") or status.get("status")))
        primary = (status.get("output") or {}).get("primary") or {}
        check("primary artifact present", bool(primary.get("storagePath")), str(primary))
        check("primary is image", primary.get("kind") == "image", str(primary.get("kind")))

        print("serve artifact:")
        if getattr(app.state.storage, "kind", "") == "local":
            url = primary.get("url", "")
            path = url.split("127.0.0.1", 1)[-1]
            if not path.startswith("/api"):
                path = url[url.find("/api"):] if "/api" in url else path
            r = client.get(path)
            check("artifact 200", r.status_code == 200, str(r.status_code))
            check("artifact non-empty", len(r.content) > 100, f"{len(r.content)} bytes")
        else:
            print("  SKIP  serve artifact (storage backend is not local)")

        print("list runs:")
        r = client.get("/api/studio/runs", headers=H)
        check("list 200", r.status_code == 200)
        check("list has run", any(x.get("run_id") == run_id for x in r.json()))

        print("quota:")
        r = client.get("/api/studio/quota", headers=H)
        q = r.json()
        check("quota 200", r.status_code == 200, str(r.status_code))
        check("quota subtracts cleanly",
              q.get("max", 0) >= 1 and q.get("remaining") == q.get("max") - q.get("used") and q.get("remaining") >= 0,
              str(q))
        check("created run counted against quota", q.get("used") >= 1, str(q.get("used")))

        print("img2video requires a ref:")
        r = client.post(
            "/api/studio/runs",
            headers=H,
            json={"tool": "img2video", "model": "wan21-i2v", "prompt": "animate", "refs": []},
        )
        check("rejects missing ref", r.status_code == 400, str(r.status_code))

        print("delete run:")
        r = client.delete(f"/api/studio/runs/{run_id}", headers=H)
        check("delete 204", r.status_code == 204, str(r.status_code))
        r = client.get(f"/api/studio/runs/{run_id}", headers=H)
        check("gone after delete", r.status_code == 404, str(r.status_code))

        print("signed artifact callback (real-GPU return path):")
        from app.workers.studio_worker import _artifact_token

        r = client.post(
            "/api/studio/runs",
            headers=H,
            json={"tool": "txt2img", "model": "sdxl", "prompt": "callback test", "refs": []},
        )
        cb_run = r.json()["run_id"]
        secret = app.state.settings.sign_secret
        token = _artifact_token(cb_run, secret)
        r = client.post(
            f"/api/studio/artifacts/{cb_run}?token={token}",
            files={"file": ("result.png", b"\x89PNG\r\n\x1a\n" + b"0" * 200, "image/png")},
        )
        check("callback accepted", r.status_code == 200, r.text[:200])
        r = client.post(
            f"/api/studio/artifacts/{cb_run}?token=WRONG",
            files={"file": ("x.png", b"data", "image/png")},
        )
        check("callback rejects bad token", r.status_code == 403, str(r.status_code))
        r = client.get(f"/api/studio/runs/{cb_run}", headers=H)
        arts = (r.json().get("output") or {}).get("artifacts") or []
        check("callback artifact stored", any(a.get("name") == "result.png" for a in arts), str(arts))

        print("video fake path (img2video with ref):")
        # Upload a tiny image to use as the source ref.
        import io

        png = _tiny_png()
        r = client.post(
            "/api/uploads",
            headers=H,
            files={"file": ("src.png", png, "image/png")},
        )
        check("upload 201", r.status_code == 201, r.text[:200])
        src_path = r.json()["items"][0]["storagePath"]
        r = client.post(
            "/api/studio/runs",
            headers=H,
            json={
                "tool": "img2video",
                "model": "wan21-i2v",
                "prompt": "subtle camera push-in",
                "params": {"width": 320, "height": 192, "frames": 24},
                "refs": [{"path": src_path, "role": "image"}],
            },
        )
        check("video run created", r.status_code == 201, r.text[:200])
        vrun = r.json()["run_id"]
        vstatus = None
        for _ in range(30):
            time.sleep(1)
            vstatus = client.get(f"/api/studio/runs/{vrun}", headers=H).json()
            if vstatus.get("status") in ("completed", "failed"):
                break
        check("video completed", vstatus.get("status") == "completed", str(vstatus.get("error") or vstatus.get("status")))
        vprimary = (vstatus.get("output") or {}).get("primary") or {}
        check("video artifact is video", vprimary.get("kind") == "video", str(vprimary.get("kind")))
        check("video has duration", (vprimary.get("duration") or 0) > 0, str(vprimary.get("duration")))

        print("img2video optional roles (start + end + reference):")
        r = client.post(
            "/api/studio/runs",
            headers=H,
            json={
                "tool": "img2video",
                "model": "wan21-flf2v",
                "prompt": "smooth morph",
                "refs": [
                    {"path": src_path, "role": "image"},
                    {"path": src_path, "role": "end_image"},
                    {"path": src_path, "role": "reference"},
                ],
            },
        )
        check("flf2v start+end+ref accepted", r.status_code == 201, r.text[:200])
        r = client.post(
            "/api/studio/runs",
            headers=H,
            json={
                "tool": "img2video",
                "model": "wan21-i2v",
                "prompt": "x",
                "refs": [{"path": src_path, "role": "end_image"}],
            },
        )
        check("missing required start rejected", r.status_code == 400, str(r.status_code))

        print("per-model ref slots:")
        mr = client.get("/api/catalog/studio", headers=H).json().get("modelRefs", {})
        i2v_roles = [s["role"] for s in mr.get("img2video", {}).get("wan21-i2v", [])]
        flf_roles = [s["role"] for s in mr.get("img2video", {}).get("wan21-flf2v", [])]
        cam_roles = [s["role"] for s in mr.get("img2video", {}).get("wan22-cam-i2v", [])]
        check("i2v slots = start + reference", i2v_roles == ["image", "reference"], str(i2v_roles))
        check("flf2v slots = start + end + reference", flf_roles == ["image", "end_image", "reference"], str(flf_roles))
        check("cam-i2v slots = start only", cam_roles == ["image"], str(cam_roles))
        r = client.post(
            "/api/studio/runs",
            headers=H,
            json={"tool": "img2video", "model": "wan22-cam-i2v", "prompt": "x",
                  "refs": [{"path": src_path, "role": "end_image"}]},
        )
        check("cam-i2v rejects end-only", r.status_code == 400, str(r.status_code))
        r = client.post(
            "/api/studio/runs",
            headers=H,
            json={"tool": "img2video", "model": "wan22-cam-i2v", "prompt": "x",
                  "refs": [{"path": src_path, "role": "image"}]},
        )
        check("cam-i2v accepts start", r.status_code == 201, r.text[:200])

        print("aspect ratio drives the real canvas (fake render, checked end to end):")
        for ratio, want_portrait in (("16:9", False), ("1:1", False), ("4:5", True)):
            r = client.post(
                "/api/studio/runs",
                headers=H,
                json={"tool": "img2video", "model": "wan21-i2v", "prompt": "a dancer",
                      "params": {"frames": 24, "fps": 16, "aspect": ratio, "fit": "pad"},
                      "refs": [{"path": src_path, "role": "image"}]},
            )
            check(f"{ratio} run accepted", r.status_code == 201, r.text[:160])
            run_id = r.json().get("run_id")
            done = {}
            for _ in range(60):
                time.sleep(1)
                done = client.get(f"/api/studio/runs/{run_id}", headers=H).json()
                if done.get("status") in ("completed", "failed"):
                    break
            check(f"{ratio} run completes", done.get("status") == "completed",
                  str(done.get("error") or done.get("status")))
            art = (done.get("output") or {}).get("primary") or {}
            aw, ah = int(art.get("width") or 0), int(art.get("height") or 0)
            check(f"{ratio} rendered landscape/portrait as asked", (ah > aw) is want_portrait,
                  f"{aw}x{ah}")
            check(f"{ratio} rendered on the 16px grid", aw % 16 == 0 and ah % 16 == 0,
                  f"{aw}x{ah}")
            num, den = (int(x) for x in ratio.split(":"))
            drift = abs((aw / float(ah)) - (num / float(den))) / (num / float(den)) * 100
            check(f"{ratio} ratio error under 2% ({drift:.2f}%)", drift < 2.0, f"{drift:.2f}%")

        r = client.post(
            "/api/studio/runs",
            headers=H,
            json={"tool": "img2video", "model": "wan21-i2v", "prompt": "a dancer",
                  "params": {"aspect": "7:3"},
                  "refs": [{"path": src_path, "role": "image"}]},
        )
        check("an unsupported ratio is refused", r.status_code == 400, str(r.status_code))

        print("library lists owned assets (uploads + studio artifacts):")
        r = client.get("/api/library", headers=H)
        check("library 200", r.status_code == 200, r.text[:200])
        items = r.json().get("items", [])
        kinds = {it["kind"] for it in items}
        paths = {it["path"] for it in items}
        check("library has image + video", {"image", "video"} <= kinds, str(kinds))
        check("library includes studio source", any(p.startswith("studio/") for p in paths),
              str(paths))
        check("library urls absolute", all(it["url"].startswith("http") for it in items))
        check("library filter works", all(
            it["kind"] == "image" for it in client.get("/api/library?kind=image", headers=H).json()["items"]
        ))

        print("deleting a run cleans up its scratch directory:")
        r = client.post(
            "/api/studio/runs",
            headers=H,
            json={"tool": "txt2img", "model": "sdxl", "prompt": "a lighthouse",
                  "params": {"aspect": "16:9", "width": 512, "height": 512}},
        )
        check("scratch-check run created", r.status_code == 201, r.text[:160])
        doomed = r.json().get("run_id")
        for _ in range(60):
            time.sleep(1)
            if client.get(f"/api/studio/runs/{doomed}", headers=H).json().get("status") in ("completed", "failed"):
                break
        work_dir = os.path.join(app.state.settings.work_root, doomed)
        check("worker left a scratch dir behind (precondition)", os.path.isdir(work_dir), work_dir)
        d = client.delete(f"/api/studio/runs/{doomed}", headers=H)
        check("delete accepted", d.status_code == 204, str(d.status_code))
        check("scratch dir removed with the run", not os.path.isdir(work_dir), work_dir)
        check("run record removed",
              client.get(f"/api/studio/runs/{doomed}", headers=H).status_code == 404)

    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("ALL STUDIO FAKE-GPU TESTS PASSED")
    return 0


def _tiny_png() -> bytes:
    """A 1x1 PNG, generated with ffmpeg if available else a hardcoded blob."""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=32x32:d=1", "-frames:v", "1", path],
            check=True,
            capture_output=True,
        )
        with open(path, "rb") as fh:
            return fh.read()
    except Exception:
        # Fallback: minimal valid 1x1 transparent PNG.
        import base64

        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        )


if __name__ == "__main__":
    raise SystemExit(main())