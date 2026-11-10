"""Recipe drafting endpoints.

Drafting is free: it only calls a text model and returns a validated plan.
The creator reviews/edits that plan in the Editor and then spends GPU time.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..auth import CurrentUser, get_current_user
from ..services import recipes as recipe_service
from ..services import workspaces as workspace_service
from ..services.recipes.base import RecipeError

router = APIRouter()


class DraftIn(BaseModel):
    workspace_id: str = Field(default="", max_length=64)
    aspect: str = Field(default="", max_length=16)
    payload: dict = Field(default_factory=dict)


def _workspace(uid: str, workspace_id: str) -> dict:
    if workspace_id:
        try:
            return workspace_service.owner_workspace(workspace_id, uid)
        except KeyError:
            raise HTTPException(status_code=404, detail="Workspace not found.")
    try:
        return workspace_service.ensure_default(uid)
    except KeyError:
        raise HTTPException(status_code=401, detail="Not signed in.")


@router.get("/recipes")
async def list_recipes(user: CurrentUser = Depends(get_current_user)):
    return {"items": recipe_service.catalog()}


@router.post("/recipes/{recipe_id}/draft")
async def draft_recipe(
    recipe_id: str,
    body: DraftIn,
    user: CurrentUser = Depends(get_current_user),
):
    module = recipe_service.get(recipe_id)
    if module is None:
        raise HTTPException(status_code=404, detail="Unknown recipe.")

    doc = _workspace(user.uid, body.workspace_id)
    workspace = workspace_service.out(doc)
    defaults = workspace.get("defaults") or {}
    aspect = body.aspect or defaults.get("aspect") or "9:16"
    payload = dict(body.payload)
    payload["workspace_id"] = body.workspace_id

    try:
        result = await run_in_threadpool(module.draft, payload, defaults, aspect)
    except RecipeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - draft must never 500 the hub
        raise HTTPException(status_code=502, detail=f"Draft failed: {exc}")

    result["recipe_id"] = module.META["id"]
    result["workspace"] = {
        "workspace_id": workspace.get("workspace_id") or "",
        "name": workspace.get("name") or "",
        "page_name": workspace.get("page_name") or "",
        "brand": workspace.get("brand") or {},
        "budget": workspace.get("budget") or {"dailyRuns": 0},
    }
    result["defaults"] = defaults
    return result
