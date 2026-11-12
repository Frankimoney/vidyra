"""GPU usage ledger — Firestore, with an in-memory fallback for local dev.

Every GPU call is one unit. Recording at submit time (not completion) is
deliberate: a run that times out or is cancelled may still have burned GPU
seconds, so counting it keeps budgets honest. Costs are estimates from a
static price table; RunPod does not return per-run billing through the API.
"""

from __future__ import annotations

import datetime
import uuid

from .firebase import firebase

COLLECTION = "usage"

#: Conservative per-run estimates in USD. Update when model pricing changes;
#: the ledger stores the number it used, so history never shifts retroactively.
PRICE_PER_RUN: dict[str, float] = {
    "sdxl": 0.012,
    "wan21-i2v": 0.090,
    "wan21-flf2v": 0.090,
    "wan22-cam-i2v": 0.120,
}
DEFAULT_PRICE = 0.050

# Dev-only fallback store: list of entry dicts.
_memory_store: list[dict] = []


def _now_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def price_for(model: str) -> float:
    return PRICE_PER_RUN.get((model or "").strip(), DEFAULT_PRICE)


def record(
    uid: str,
    *,
    workspace_id: str = "",
    kind: str = "studio",
    tool: str = "",
    model: str = "",
    units: int = 1,
    job_id: str = "",
    reference: str = "",
    estimated_cost: float | None = None,
) -> None:
    """Append one usage entry. Never raises — accounting must not fail a job."""
    if not uid:
        return
    units = max(1, int(units or 1))
    entry = {
        "usageId": uuid.uuid4().hex[:16],
        "userId": uid,
        "workspaceId": workspace_id or "",
        "kind": kind,
        "tool": tool or "",
        "model": model or "",
        "units": units,
        "jobId": job_id or "",
        "reference": reference or "",
        "estimatedCost": float(
            estimated_cost if estimated_cost is not None else price_for(model) * units
        ),
        "createdAt": _now_stamp(),
    }
    try:
        if firebase.db is None:
            _memory_store.append(entry)
        else:
            firebase.db.collection(COLLECTION).add(entry)
    except Exception:
        # Local fallback so a Firestore hiccup doesn't lose the record entirely.
        _memory_store.append(entry)


def _entries(uid: str, limit: int = 1000) -> list[dict]:
    if firebase.db is None:
        items = [dict(e) for e in _memory_store if e.get("userId") == uid]
    else:
        items = [
            snap.to_dict()
            for snap in firebase.db.collection(COLLECTION)
            .where("userId", "==", uid)
            .limit(limit)
            .stream()
        ]
        # Keep the local fallback in sync so a mid-session dev switch still sees it.
        items += [dict(e) for e in _memory_store if e.get("userId") == uid]
    items.sort(key=lambda e: e.get("createdAt") or "", reverse=True)
    return items


def daily_runs(uid: str, workspace_id: str = "") -> int:
    today = _now_stamp()[:10]
    return sum(
        int(e.get("units") or 1)
        for e in _entries(uid)
        if (e.get("createdAt") or "").startswith(today)
        and (not workspace_id or e.get("workspaceId") == workspace_id)
    )


def summary(uid: str, workspace_id: str = "", days: int = 30) -> dict:
    cutoff = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=max(1, int(days)))
    ).isoformat()
    items = [
        e
        for e in _entries(uid)
        if (e.get("createdAt") or "") >= cutoff
        and (not workspace_id or e.get("workspaceId") == workspace_id)
    ]

    by_model: dict[str, dict] = {}
    total_runs = 0
    total_cost = 0.0
    for entry in items:
        units = int(entry.get("units") or 1)
        cost = float(entry.get("estimatedCost") or 0.0)
        total_runs += units
        total_cost += cost
        key = entry.get("model") or "unknown"
        bucket = by_model.setdefault(key, {"model": key, "runs": 0, "cost": 0.0})
        bucket["runs"] += units
        bucket["cost"] = round(bucket["cost"] + cost, 4)

    return {
        "days": max(1, int(days)),
        "workspace_id": workspace_id,
        "today_runs": daily_runs(uid, workspace_id),
        "runs": total_runs,
        "estimated_cost": round(total_cost, 4),
        "by_model": sorted(by_model.values(), key=lambda b: b["runs"], reverse=True),
        "recent": items[:20],
    }
