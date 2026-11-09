"""GPU usage and estimated spend."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..auth import CurrentUser, get_current_user
from ..services import usage as usage_service

router = APIRouter()


@router.get("/usage/summary")
async def usage_summary(
    workspace_id: str = Query(default="", max_length=64),
    days: int = Query(default=30, ge=1, le=365),
    user: CurrentUser = Depends(get_current_user),
):
    return usage_service.summary(user.uid, workspace_id=workspace_id, days=days)
