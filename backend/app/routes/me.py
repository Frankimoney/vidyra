"""Current-user profile endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth import CurrentUser, get_current_user
from ..models import ProfileIn
from ..services import profiles

router = APIRouter()


@router.get("/me/profile")
async def get_profile(user: CurrentUser = Depends(get_current_user)):
    return {"page_name": profiles.get_page_name(user.uid)}


@router.put("/me/profile")
async def set_profile(
    payload: ProfileIn,
    user: CurrentUser = Depends(get_current_user),
):
    page_name = (payload.page_name or "").strip()[:64]
    profiles.set_page_name(user.uid, page_name)
    return {"page_name": page_name}