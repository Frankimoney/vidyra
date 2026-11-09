"""Simple in-memory per-user rate limiter."""

from __future__ import annotations

import time

from fastapi import HTTPException, status


class RateLimiter:
    def __init__(self):
        self._calls: dict[str, list[float]] = {}

    def check(self, uid: str, limit_per_min: int):
        now = time.monotonic()
        window = self._calls.setdefault(uid, [])
        cutoff = now - 60.0
        window[:] = [t for t in window if t > cutoff]
        if len(window) >= limit_per_min:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Try again shortly.",
            )
        window.append(now)


rate_limiter = RateLimiter()