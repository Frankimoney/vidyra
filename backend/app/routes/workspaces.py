"""Brand workspace endpoints.

A workspace holds the look and defaults a creator reuses across videos.
The first one is seeded automatically, so the app works in single-brand
(private) mode without any setup, while the API already supports many.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import CurrentUser, get_current_user
from ..models import WorkspaceIn, WorkspaceMemberIn, WorkspacePatch
from ..services import workspaces as workspace_service

router = APIRouter()


def _brand_dict(brand) -> dict:
    if brand is None:
        return {}
    data = brand.model_dump() if hasattr(brand, "model_dump") else dict(brand)
    return {
        "primary": data.get("primary", ""),
        "secondary": data.get("secondary", ""),
        "logoPath": data.get("logo_path") or data.get("logoPath") or "",
    }


def _payload_dict(payload: WorkspaceIn | WorkspacePatch) -> dict:
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "brand" in data:
        data["brand"] = _brand_dict(getattr(payload, "brand", None))
    if "defaults" in data and isinstance(data["defaults"], dict):
        data["defaults"] = {k: v for k, v in data["defaults"].items() if v is not None}
    if "daily_runs" in data:
        data["budget"] = {"dailyRuns": data.pop("daily_runs")}
    return data


@router.get("/workspaces")
async def list_workspaces(user: CurrentUser = Depends(get_current_user)):
    items = workspace_service.list_workspaces(user.uid)
    return {"items": [workspace_service.out(doc) for doc in items]}


@router.post("/workspaces", status_code=status.HTTP_201_CREATED)
async def create_workspace(
    payload: WorkspaceIn,
    user: CurrentUser = Depends(get_current_user),
):
    doc = workspace_service.create_workspace(user.uid, _payload_dict(payload))
    return workspace_service.out(doc)


@router.get("/workspaces/{workspace_id}")
async def get_workspace(
    workspace_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        doc = workspace_service.owner_workspace(workspace_id, user.uid)
    except KeyError:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return workspace_service.out(doc)


@router.patch("/workspaces/{workspace_id}")
async def update_workspace(
    workspace_id: str,
    payload: WorkspacePatch,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        doc = workspace_service.update_workspace(
            user.uid, workspace_id, _payload_dict(payload)
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return workspace_service.out(doc)


@router.post("/workspaces/{workspace_id}/members")
async def add_workspace_member(
    workspace_id: str,
    payload: WorkspaceMemberIn,
    user: CurrentUser = Depends(get_current_user),
):
    member_uid = (payload.uid or "").strip()
    email = (payload.email or "").strip().lower()
    if not member_uid and email:
        try:
            from firebase_admin import auth as firebase_auth

            member_uid = firebase_auth.get_user_by_email(email).uid
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Could not find that email. Ask them to sign in first, or use their user id.",
            )
    if not member_uid:
        raise HTTPException(status_code=400, detail="Add an email or user id.")
    try:
        doc = workspace_service.add_member(
            user.uid, workspace_id, member_uid, email, payload.role
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return workspace_service.out(doc)


@router.delete("/workspaces/{workspace_id}/members/{member_uid}")
async def remove_workspace_member(
    workspace_id: str,
    member_uid: str,
    user: CurrentUser = Depends(get_current_user),
):
    try:
        doc = workspace_service.remove_member(user.uid, workspace_id, member_uid)
    except KeyError:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return workspace_service.out(doc)
