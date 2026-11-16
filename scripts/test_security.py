"""Local checks for the security boundary: signed URLs, share tokens,
storage-path traversal.

These are the two places an unauthenticated remote can feed us attacker bytes
(a signed media link, a published share token) and the one place a storage path
reaches the disk, so the contract here is "answer 403/404, never a 500, never
someone else's file". No GPU, no ffmpeg, no network. Run:
python scripts/test_security.py
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "backend")):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.services import shares  # noqa: E402
from app.services.storage.base import (  # noqa: E402
    StorageError,
    _safe_resolve,
    signed_media_path,
    verify_signed_url,
)

SECRET = "test-secret"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        failures.append(name)


def raises_storage_error(path: str) -> bool:
    try:
        _safe_resolve("C:/store", path)
        return False
    except StorageError:
        return True


def test_share_tokens():
    print("share tokens:")
    token = shares.create_share_token("job-42", SECRET)
    check("valid token round-trips", shares.parse_share_token(token, SECRET) == "job-42")

    flipped = token[:-1] + ("0" if token[-1] != "0" else "1")
    check("one flipped char -> rejected", shares.parse_share_token(flipped, SECRET) is None)

    check("signature missing -> rejected", shares.parse_share_token("job-42.2592000000.", SECRET) is None)
    check("malformed token -> rejected", shares.parse_share_token("justonepart", SECRET) is None)

    # `compare_digest` raises TypeError on str operands that contain non-ASCII:
    # a 24-char "é" sig must come back as None, never blow up the endpoint.
    non_ascii = "é" * 24
    sig = f"job-42.2592000000.{non_ascii}"
    try:
        ok = shares.parse_share_token(sig, SECRET) is None
    except TypeError:
        ok = False
    check("non-ASCII signature -> None, not a 500", ok)

    alpha_sig = "a" * 23 + "g"
    check("wrong-shape signature -> rejected",
          shares.parse_share_token(f"job-42.2592000000.{alpha_sig}", SECRET) is None)

    expired = f"job-42.0.{shares._sign('job-42', 0, SECRET)}"
    check("expired token -> rejected", shares.parse_share_token(expired, SECRET) is None)

    check("a different secret cannot read a token",
          shares.parse_share_token(token, "other-secret") is None)


def test_signed_urls():
    print("signed media URLs:")
    url = signed_media_path("media/u/clip.mp4", 60, SECRET)
    exp = int(url.split("exp=")[1].split("&")[0])
    sig = url.split("sig=")[1]
    check("fresh signature verifies", verify_signed_url("media/u/clip.mp4", exp, sig, SECRET))

    check("tampered signature fails", verify_signed_url("media/u/clip.mp4", exp, "0" * 24, SECRET) is False)
    check("different payload fails",
          verify_signed_url("media/OTHER/clip.mp4", exp, sig, SECRET) is False)

    for bad in ("é" * 24, "a" * 23 + "g", None, "", "AAAA"):
        try:
            ok = verify_signed_url("media/u/clip.mp4", exp, bad, SECRET) is False
        except TypeError:
            ok = False
        check(f"non-hex signature {str(bad)[:6]!r} -> denied, no TypeError", ok)


def test_path_traversal():
    print("storage path traversal:")

    # Deleting / serving any `..` segment must be refused *textually*: the
    # authorisation is `startswith("media/<uid>/")`, which traversal strings
    # satisfy while still escaping the caller's own directory.
    for bad in (
        "media/u/../other/f.mp4",
        "media/u/../../../etc/passwd",
        "../../etc/passwd",
        "../u/x.mp4",
        r"media\u\..\v.mp4",
        r"..\..\etc\passwd",
    ):
        check(f"refuses {bad!r}", raises_storage_error(bad))

    check("plain sibling path allowed", not raises_storage_error("media/u/a.mp4"))
    check("backslash normalised plain path allowed", not raises_storage_error(r"media\u\a.mp4"))


def test_owned_media_guard():
    """The library delete/ownership guard rejects traversal and foreign files."""
    print("library ownership guard:")
    from fastapi import HTTPException

    from app.routes.uploads import _owned_media_path

    def code(path: str, uid: str = "dev-user"):
        try:
            _owned_media_path(path, uid)
            return 200
        except HTTPException as exc:
            return exc.status_code

    check("own upload accepted", code("media/dev-user/vid/1.png") == 200)
    check("own studio out accepted", code("studio/dev-user/run/x.png") == 200)
    check("own render accepted", code("videos/dev-user/abc.mp4") == 200)
    check("foreign upload rejected", code("media/otheruser/1.png") == 403)
    check("no prefix rejected", code("secret.txt") == 403)
    check("dotdot rejected", code("media/dev-user/../x.png") == 400)
    check("backslash rejected", code("media/dev-user\\x.png") == 400)
    check("empty path rejected", code("") == 400)
    check("empty segment rejected", code("media/dev-user//x.png") == 400)


def test_production_hardening():
    """Verify series media ownership, film package ref ownership, quota soft-delete, and studio_local transcribe guard."""
    print("production hardening guards:")
    import json
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.routes.films import _validate_outline_ownership
    from app.routes.series import validate_series_payload
    from app.services import studio as studio_service
    from app.services.transcribe import make_transcriber
    from app.config import Settings

    s = Settings()

    # 1. Series payload ownership
    try:
        validate_series_payload(
            {"script": "hi", "media": ["media/other-user/secret.mp4"]},
            "dev-user",
            storage=None,
            s=s,
        )
        check("series rejects foreign media", False)
    except HTTPException as exc:
        check("series rejects foreign media", exc.status_code == 400)

    # 2. Film package reference_image & clone voice ownership
    try:
        _validate_outline_ownership(
            {"scenes": [{"reference_image": "media/other-user/pic.png"}]},
            "dev-user",
        )
        check("film outline rejects foreign reference_image", False)
    except HTTPException as exc:
        check("film outline rejects foreign reference_image", exc.status_code == 400)

    try:
        _validate_outline_ownership(
            {"characters": [{"name": "Host", "voice": "clone:media/other-user/v.wav"}]},
            "dev-user",
        )
        check("film outline rejects foreign clone voice", False)
    except HTTPException as exc:
        check("film outline rejects foreign clone voice", exc.status_code == 400)

    # 3. Deleting a Studio run does not refund daily quota
    studio_service._memory_store.clear()
    rid = studio_service.create_run("quota-user", "txt2img", "sdxl", "cat", {}, [])
    check("daily_used counts created run", studio_service.daily_used("quota-user") == 1)
    studio_service.delete_run(rid)
    check("deleted run hidden from get_run", studio_service.get_run(rid) is None)
    check("deleted run hidden from list_runs", studio_service.list_runs("quota-user") == [])
    check("daily_used preserved after delete_run", studio_service.daily_used("quota-user") == 1)
    studio_service._memory_store.clear()

    # 4. Merged-pod studio_local transcribe guard avoids self-deadlock
    local_settings = SimpleNamespace(
        whisper_backend="runpod",
        studio_local=True,
        runpod_enabled=True,
        runpod_fake=False,
    )
    fn = make_transcriber("u1", "", None, local_settings)
    check("studio_local transcribe returns None immediately", fn("any.wav") is None)


def main() -> int:
    test_share_tokens()
    test_signed_urls()
    test_path_traversal()
    test_owned_media_guard()
    test_production_hardening()
    print()
    if failures:
        print(f"FAILED: {len(failures)} -> {failures}")
        return 1
    print("ALL SECURITY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())