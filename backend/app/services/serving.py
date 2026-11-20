"""
Choose and load the model the API serves: MLflow registry alias or the bundled files
"""
import logging
from pathlib import Path
from typing import Tuple

from app.core.config import settings
from app.core.security import utcnow
from app.services.model_loader import ModelLoader

logger = logging.getLogger(__name__)


def load_local() -> Tuple[ModelLoader, dict]:
    loader = ModelLoader()
    model_path = Path(settings.MODEL_DIR) / settings.DEFAULT_MODEL
    loader.load_from_files(
        config_file=model_path / "model_config.json",
        taxonomy_file=model_path / "taxonomy_map.json",
        weights_file=model_path / "best_model.pth",
        novelty_file=model_path / "novelty" / "novelty_detector.pth",
    )
    config = loader.model_config
    return loader, {
        "source": "local",
        "uri": str(model_path),
        "name": settings.DEFAULT_MODEL,
        "alias": None,
        "version": str(config.get("model_version", "bundled")),
        "run_id": None,
        "metrics": {"val_accuracy": config["best_accuracy"]} if "best_accuracy" in config else {},
    }


def load_from_mlflow() -> Tuple[ModelLoader, dict]:
    from app.ml import registry  # MLflow is only imported when it is used

    registry.configure(settings.MLFLOW_TRACKING_URI)
    return registry.load_registered(settings.MLFLOW_MODEL_NAME, settings.MLFLOW_MODEL_ALIAS)


def mlflow_enabled() -> bool:
    return settings.MODEL_SOURCE.lower() in ("mlflow", "auto") and bool(settings.MLFLOW_TRACKING_URI)


def load_serving_model() -> Tuple[ModelLoader, dict]:
    """
    MODEL_SOURCE=mlflow: registry only (fail fast); local: bundled files;
    auto: registry when MLFLOW_TRACKING_URI is set, falling back to the bundled files
    """
    source = settings.MODEL_SOURCE.lower()
    if source not in ("mlflow", "local", "auto"):
        raise ValueError(f"MODEL_SOURCE must be mlflow, local or auto (got {settings.MODEL_SOURCE!r})")
    if source == "mlflow" and not settings.MLFLOW_TRACKING_URI:
        raise RuntimeError("MODEL_SOURCE=mlflow requires MLFLOW_TRACKING_URI")

    if mlflow_enabled():
        try:
            loader, info = load_from_mlflow()
        except Exception as e:
            if source == "mlflow":
                raise RuntimeError(f"Could not load {settings.MLFLOW_MODEL_NAME}@{settings.MLFLOW_MODEL_ALIAS} from MLflow: {e}")
            logger.warning(f"MLflow model unavailable ({e}); falling back to the bundled model")
        else:
            info["loaded_at"] = utcnow().isoformat()
            logger.info(f"Serving {info['uri']} (version {info['version']}) from {settings.MLFLOW_TRACKING_URI}")
            return loader, info

    loader, info = load_local()
    info["loaded_at"] = utcnow().isoformat()
    logger.info(f"Serving bundled model from {info['uri']}")
    return loader, info


def cache_prefix(info: dict) -> str:
    """Prediction cache keys include the model version, so a new champion never serves stale results"""
    return f"pred:{info.get('source')}-{info.get('version')}"


def sync_version_registry(version_registry, info: dict):
    """Mirror the served model in the app's version registry and mark it current"""
    tag = f"{info['source']}:{info['name']}:{info['version']}"
    existing = next((v for v in version_registry.versions.values() if tag in v.tags), None)
    if existing is None:
        metrics = dict(info.get("metrics") or {})
        if "val_accuracy" in metrics:
            metrics.setdefault("accuracy", metrics.pop("val_accuracy"))
        is_mlflow = info["source"] == "mlflow"
        existing = version_registry.register_version(
            version_name=f"{info['name']} v{info['version']}" if is_mlflow else str(info["version"]),
            model_path=info["uri"],
            description=(
                f"MLflow registered model, served via @{info['alias']}" if is_mlflow else "Bundled model files"
            ),
            metrics={k: float(v) for k, v in metrics.items()},
            training_info={"run_id": info.get("run_id"), "source": info["source"]},
            tags=[tag, info["source"]] + ([f"@{info['alias']}"] if info.get("alias") else []),
        )
    if not existing.is_current:
        version_registry.set_current_version(existing.version_id)
    return existing
