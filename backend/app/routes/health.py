"""Health and system endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..models import HealthOut
from ..services.firebase import firebase

__version__ = "0.1.0"

router = APIRouter()


@router.get("/health", response_model=HealthOut)
async def health(request: Request):
    return HealthOut(
        status="ok",
        version=__version__,
        firebase=firebase.ready,
        storage=(request.app.state.storage.kind if hasattr(request.app.state, "storage") else None),
    )