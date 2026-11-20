"""
A/B testing experiment endpoints
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import require_permission
from app.api.system import state
from app.core.security import Permission
from app.db.models import User

router = APIRouter(prefix="/experiments", tags=["A/B Testing"])

can_view = require_permission(Permission.EXPERIMENT_VIEW)
can_manage = require_permission(Permission.EXPERIMENT_MANAGE)


def manager(request: Request):
    return state(request, "ab_test_manager")


@router.get("")
def list_experiments(request: Request, status: Optional[str] = None, _: User = Depends(can_view)):
    experiments = manager(request).list_experiments(status=status)
    return {"total": len(experiments), "experiments": experiments}


@router.get("/active")
def get_active_experiment(request: Request, _: User = Depends(can_view)):
    experiment = manager(request).get_active_experiment()
    if not experiment:
        return {"active": False, "experiment": None}
    return {
        "active": True,
        "experiment": {
            "experiment_id": experiment.experiment_id,
            "experiment_name": experiment.experiment_name,
            "status": experiment.status,
            "variants": [
                {
                    "variant_id": v.variant_id,
                    "variant_name": v.variant_name,
                    "version_id": v.version_id,
                    "traffic_percentage": v.traffic_percentage,
                    "prediction_count": v.prediction_count,
                }
                for v in experiment.variants
            ],
        },
    }


@router.post("/create")
def create_experiment(
    request: Request,
    experiment_name: str,
    description: str,
    variant_configs: List[Dict[str, Any]],
    strategy: str = "random",
    duration_days: Optional[int] = None,
    _: User = Depends(can_manage),
):
    """
    Create an A/B test experiment, e.g. variant_configs:
    [{"name": "control", "version_id": "v2.0", "traffic": 50}, {"name": "treatment", "version_id": "v2.1", "traffic": 50}]
    """
    try:
        experiment = manager(request).create_experiment(
            experiment_name=experiment_name,
            description=description,
            variant_configs=variant_configs,
            strategy=strategy,
            duration_days=duration_days,
        )
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid experiment: {e}")
    return {
        "status": "success",
        "message": "Experiment created successfully",
        "experiment_id": experiment.experiment_id,
        "experiment_name": experiment.experiment_name,
        "variants": len(experiment.variants),
    }


@router.get("/{experiment_id}/results")
def get_experiment_results(experiment_id: str, request: Request, _: User = Depends(can_view)):
    try:
        return manager(request).get_experiment_results(experiment_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{experiment_id}/start")
def start_experiment(experiment_id: str, request: Request, _: User = Depends(can_manage)):
    if not manager(request).start_experiment(experiment_id):
        raise HTTPException(status_code=400, detail="Cannot start experiment (not found or another experiment is active)")
    return {"status": "success", "message": f"Experiment {experiment_id} started", "experiment_id": experiment_id}


@router.post("/{experiment_id}/stop")
def stop_experiment(experiment_id: str, request: Request, declare_winner: bool = True, _: User = Depends(can_manage)):
    ab = manager(request)
    if not ab.stop_experiment(experiment_id, declare_winner=declare_winner):
        raise HTTPException(status_code=404, detail="Experiment not found")
    results = ab.get_experiment_results(experiment_id)
    return {
        "status": "success",
        "message": f"Experiment {experiment_id} stopped",
        "experiment_id": experiment_id,
        "winner": results.get("winner"),
        "results": results,
    }


@router.post("/{experiment_id}/pause")
def pause_experiment(experiment_id: str, request: Request, _: User = Depends(can_manage)):
    if not manager(request).pause_experiment(experiment_id):
        raise HTTPException(status_code=404, detail="Experiment not found")
    return {"status": "success", "message": f"Experiment {experiment_id} paused", "experiment_id": experiment_id}


@router.post("/{experiment_id}/resume")
def resume_experiment(experiment_id: str, request: Request, _: User = Depends(can_manage)):
    if not manager(request).resume_experiment(experiment_id):
        raise HTTPException(status_code=400, detail="Cannot resume experiment (not found or not paused)")
    return {"status": "success", "message": f"Experiment {experiment_id} resumed", "experiment_id": experiment_id}
