"""
Health, monitoring and cache endpoints
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.security import Permission
from app.db.models import User
from app.db.session import get_db

router = APIRouter()


def state(request: Request, name: str):
    value = getattr(request.app.state, name, None)
    if value is None:
        raise HTTPException(status_code=503, detail=f"{name} not initialized")
    return value


def database_ok(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@router.get("/health", tags=["Health"])
def health_check(request: Request, db: Session = Depends(get_db)):
    """Public liveness/readiness probe (no internal details)"""
    model_loader = getattr(request.app.state, "model_loader", None)
    model_ok = bool(model_loader and model_loader.is_loaded())
    db_ok = database_ok(db)
    healthy = model_ok and db_ok
    return JSONResponse(
        status_code=200 if healthy else 503,  # load balancers act on the status code
        content={
            "status": "healthy" if healthy else "degraded",
            "model_loaded": model_ok,
            "database": "ok" if db_ok else "unavailable",
        },
    )


@router.get("/health/detailed", tags=["Health"])
def detailed_health_check(
    request: Request,
    _: User = Depends(require_permission(Permission.MONITORING_VIEW)),
    db: Session = Depends(get_db),
):
    """Comprehensive health check with all components"""
    health = state(request, "monitoring_manager").get_comprehensive_health(
        model_loader=getattr(request.app.state, "model_loader", None),
        cache_manager=getattr(request.app.state, "cache_manager", None),
    )
    model_info = getattr(request.app.state, "model_info", None) or {}
    health["components"]["model"].update({
        "source": model_info.get("source"),
        "name": model_info.get("name"),
        "version": model_info.get("version"),
        "alias": model_info.get("alias"),
    })
    db_ok = database_ok(db)
    health["components"]["database"] = {"status": "active" if db_ok else "error"}
    if not db_ok:
        health["status"] = "degraded"
    return health


# ==================== Monitoring ====================

@router.get("/monitoring/system", tags=["Monitoring"])
def get_system_metrics(request: Request, _: User = Depends(require_permission(Permission.MONITORING_VIEW))):
    """System-level metrics (CPU, memory, disk)"""
    return state(request, "monitoring_manager").get_system_health()


@router.get("/monitoring/application", tags=["Monitoring"])
def get_application_metrics(request: Request, _: User = Depends(require_permission(Permission.MONITORING_VIEW))):
    """Application-level metrics"""
    return state(request, "monitoring_manager").get_application_health()


@router.get("/monitoring/dashboard", tags=["Monitoring"])
def get_dashboard_data(request: Request, _: User = Depends(require_permission(Permission.MONITORING_VIEW))):
    """All metrics formatted for the monitoring dashboard"""
    data = state(request, "monitoring_manager").get_dashboard_metrics()
    cache_manager = getattr(request.app.state, "cache_manager", None)
    if cache_manager:
        data["cache"] = cache_manager.get_stats()
    model_loader = getattr(request.app.state, "model_loader", None)
    loaded = bool(model_loader and model_loader.is_loaded())
    data["model"] = {"loaded": loaded, "status": "active" if loaded else "inactive"}
    return data


# ==================== Cache ====================

@router.get("/cache/stats", tags=["Cache"])
def get_cache_stats(request: Request, _: User = Depends(require_permission(Permission.CACHE_VIEW))):
    return state(request, "cache_manager").get_stats()


@router.get("/cache/backend", tags=["Cache"])
def get_cache_backend(request: Request, _: User = Depends(require_permission(Permission.CACHE_VIEW))):
    backend = state(request, "cache_manager").backend
    return {"backend": backend, "description": "Redis" if backend == "redis" else "In-memory (fallback)"}


@router.post("/cache/clear", tags=["Cache"])
def clear_cache(
    request: Request,
    pattern: Optional[str] = None,
    _: User = Depends(require_permission(Permission.CACHE_MANAGE)),
):
    """Clear cache entries (optionally by pattern)"""
    count = state(request, "cache_manager").clear(pattern)
    return {"status": "success", "cleared_keys": count, "pattern": pattern}
