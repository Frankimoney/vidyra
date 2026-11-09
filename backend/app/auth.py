"""Authentication dependencies.

Verifies Firebase ID tokens presented as `Authorization: Bearer <token>`.
In development mode, accepts a static dev key so the API is testable
before Firebase credentials are configured.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import Settings
from .services.firebase import firebase

bearer = HTTPBearer(auto_error=False)


class CurrentUser:
    def __init__(self, uid: str, token: str, dev: bool = False):
        self.uid = uid
        self.token = token
        self.is_dev = dev


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token.",
        )

    token = credentials.credentials
    s: Settings = request.app.state.settings

    # Development-only bypass
    if s.dev_mode and s.dev_api_key and token == s.dev_api_key:
        return CurrentUser(uid="dev-user", token=token, dev=True)

    if not firebase.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication backend is not configured.",
        )

    try:
        from firebase_admin import auth

        decoded = auth.verify_id_token(token)
        return CurrentUser(uid=decoded.get("uid", ""), token=token)
    except Exception as exc:  # pragma: no cover - depends on Firebase SDK
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        ) from exc