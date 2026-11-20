"""
Model version registry endpoints
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request

from app.api.deps import require_permission
from app.api.system import state
from app.core.security import Permission
from app.db.models import User

router = APIRouter(prefix="/versions", tags=["Model Versions"])

can_view = require_permission(Permission.VERSION_VIEW)
can_manage = require_permission(Permission.VERSION_MANAGE)


def registry(request: Request):
    return state(request, "version_registry")


def summary(v) -> dict:
    return {
        "version_id": v.version_id,
        "version_name": v.version_name,
        "created_at": v.created_at,
        "status": v.status,
        "is_current": v.is_current,
        "description": v.description,
        "metrics": v.metrics,
        "tags": v.tags,
        "checksum": v.checksum,
    }


@router.get("")
def list_model_versions(request: Request, status: Optional[str] = None, limit: int = 20, _: User = Depends(can_view)):
    """List registered model versions with optional status filter"""
    versions = registry(request).list_versions(status=status)[:limit]
    return {"total": len(versions), "versions": [summary(v) for v in versions]}


@router.get("/current/info")
def get_current_version(request: Request, _: User = Depends(can_view)):
    """Currently active model version"""
    current = registry(request).get_current_version()
    if not current:
        raise HTTPException(status_code=404, detail="No current version set")
    return summary(current)


@router.get("/history")
def get_version_history(request: Request, limit: int = 10, _: User = Depends(can_view)):
    history = registry(request).get_version_history(limit=limit)
    return {"total": len(history), "history": history}


@router.get("/statistics")
def get_version_statistics(request: Request, _: User = Depends(can_view)):
    stats = registry(request).get_statistics()
    stats.pop("registry_path", None)  # internal detail
    return stats


@router.get("/compare/{version_id_1}/{version_id_2}")
def compare_versions(version_id_1: str, version_id_2: str, request: Request, _: User = Depends(can_view)):
    try:
        return registry(request).compare_versions(version_id_1, version_id_2)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/register")
def register_new_version(
    request: Request,
    version_name: str,
    model_path: str,
    description: str = "",
    metrics: Optional[Dict[str, float]] = Body(None),
    training_info: Optional[Dict[str, Any]] = Body(None),
    tags: Optional[List[str]] = Body(None),
    set_as_current: bool = False,
    _: User = Depends(can_manage),
):
    """Register a new model version"""
    version = registry(request).register_version(
        version_name=version_name,
        model_path=model_path,
        description=description,
        metrics=metrics,
        training_info=training_info,
        tags=tags,
        set_as_current=set_as_current,
    )
    return {
        "status": "success",
        "message": "Version registered successfully",
        "version_id": version.version_id,
        "version_name": version.version_name,
        "is_current": version.is_current,
    }


# Declared after the static paths above so they aren't captured as version IDs
@router.get("/{version_id}")
def get_version_details(version_id: str, request: Request, _: User = Depends(can_view)):
    version = registry(request).get_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Version not found")
    return {**summary(version), "model_path": version.model_path, "training_info": version.training_info}


@router.post("/{version_id}/activate")
def activate_version(version_id: str, request: Request, _: User = Depends(can_manage)):
    if not registry(request).set_current_version(version_id):
        raise HTTPException(status_code=404, detail="Version not found")
    return {"status": "success", "message": f"Version {version_id} activated", "version_id": version_id}


@router.post("/{version_id}/rollback")
def rollback_version(version_id: str, request: Request, _: User = Depends(can_manage)):
    if not registry(request).rollback_to_version(version_id):
        raise HTTPException(status_code=404, detail="Version not found or rollback failed")
    return {"status": "success", "message": f"Rolled back to version {version_id}", "version_id": version_id}


@router.post("/{version_id}/archive")
def archive_version(version_id: str, request: Request, _: User = Depends(can_manage)):
    if not registry(request).archive_version(version_id):
        raise HTTPException(status_code=400, detail="Cannot archive version (not found or is current)")
    return {"status": "success", "message": f"Version {version_id} archived", "version_id": version_id}


@router.post("/{version_id}/deprecate")
def deprecate_version(version_id: str, request: Request, reason: str = "", _: User = Depends(can_manage)):
    if not registry(request).deprecate_version(version_id, reason=reason):
        raise HTTPException(status_code=400, detail="Cannot deprecate version (not found or is current)")
    return {
        "status": "success",
        "message": f"Version {version_id} deprecated",
        "version_id": version_id,
        "reason": reason,
    }
