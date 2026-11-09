"""Share tokens: HMAC-signed job references good for public watch pages."""

from __future__ import annotations

import hashlib
import hmac
import re
import time

SHARE_TTL_SECONDS = 7 * 24 * 3600  # matches published media link lifetime

# `_sign` truncates to 24 hex chars, so a valid signature is always that shape.
_HEX_SIG = re.compile(r"[0-9a-f]{24}")


def create_share_token(job_id: str, secret: str, ttl: int = SHARE_TTL_SECONDS) -> str:
    exp = int(time.time()) + ttl
    return f"{job_id}.{exp}.{_sign(job_id, exp, secret)}"


def parse_share_token(token: str, secret: str) -> str | None:
    """Return the job_id when the token is valid, else None."""
    try:
        job_id, exp_s, sig = (token or "").split(".")
        exp = int(exp_s)
        if int(time.time()) > exp:
            return None
        # `_sign` emits lowercase hex, so anything else is forged. Checking the
        # shape first also keeps a non-ASCII `sig` from reaching
        # `compare_digest`, which raises TypeError on such strings -- an
        # unauthenticated endpoint must answer 404, never a 500.
        if not _HEX_SIG.fullmatch(sig or ""):
            return None
        if not hmac.compare_digest(sig, _sign(job_id, exp, secret)):
            return None
    except (ValueError, TypeError):
        return None
    return job_id if job_id else None


def _sign(job_id: str, exp: int, secret: str) -> str:
    payload = f"{job_id}:{exp}".encode()
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()[:24]