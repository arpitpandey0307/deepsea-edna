"""
MLflow Model Registry integration

Lifecycle:  train -> log run (params, metrics, artifacts) -> register a new version
            -> alias it "challenger" -> approval gate -> move "champion" alias
            -> the API serves models:/<name>@champion
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import mlflow
import pandas as pd
from mlflow import MlflowClient
from mlflow.entities.model_registry import ModelVersion
from mlflow.exceptions import MlflowException

logger = logging.getLogger(__name__)

CHAMPION = "champion"
CHALLENGER = "challenger"
GATE_METRIC = "val_accuracy"

PYFUNC_CODE = Path(__file__).with_name("pyfunc_model.py")
# Artifact keys used by MarineEDNAModel.load_context -> file names in a model folder
MODEL_FILES = {
    "weights": "best_model.pth",
    "model_config": "model_config.json",
    "taxonomy_map": "taxonomy_map.json",
}
EXAMPLE_SEQUENCE = (
    "ATGGCAAGCCTACGATTCCTAGGCCTTCTAACCCTAACCATCCTCATCACAGCCATACTGCTAGCTCTTCACTACACCGC"
    "CGACACAACAACCGCCTTTTCATCCATCGCCCACATCTGCCGCGACGTTAACTACGGCTGACTGATCCG"
)


def configure(tracking_uri: str):
    if not tracking_uri:
        raise ValueError("MLFLOW_TRACKING_URI is not set")
    mlflow.set_tracking_uri(tracking_uri)


def _pip_requirements():
    import numpy
    import torch
    return [
        f"torch=={torch.__version__.split('+')[0]}",
        f"numpy=={numpy.__version__}",
        f"pandas=={pd.__version__}",
        "pydantic-settings",
    ]


def read_model_config(model_dir: Path) -> dict:
    return json.loads((Path(model_dir) / MODEL_FILES["model_config"]).read_text())


def log_model_version(model_dir: Path, model_name: str, alias: Optional[str] = None) -> ModelVersion:
    """
    Log the model folder as a pyfunc model in the active run and register it as a new version
    """
    model_dir = Path(model_dir)
    missing = [f for f in MODEL_FILES.values() if not (model_dir / f).exists()]
    if missing:
        raise FileNotFoundError(f"Model folder {model_dir} is missing {missing}")
    if mlflow.active_run() is None:
        raise RuntimeError("log_model_version must be called inside mlflow.start_run()")

    info = mlflow.pyfunc.log_model(
        name="model",
        python_model=str(PYFUNC_CODE),
        artifacts={key: str(model_dir / filename) for key, filename in MODEL_FILES.items()},
        input_example=pd.DataFrame({"sequence": [EXAMPLE_SEQUENCE]}),
        pip_requirements=_pip_requirements(),
        registered_model_name=model_name,
    )
    client = MlflowClient()
    version = client.get_model_version(model_name, str(info.registered_model_version))
    if alias:
        client.set_registered_model_alias(model_name, alias, version.version)
    logger.info(f"Registered {model_name} version {version.version}" + (f" as @{alias}" if alias else ""))
    return version


NOT_FOUND_CODES = {"RESOURCE_DOES_NOT_EXIST", "INVALID_PARAMETER_VALUE"}  # unknown model / unknown alias


def get_alias_version(model_name: str, alias: str) -> Optional[ModelVersion]:
    """The version behind an alias, or None if the model/alias doesn't exist yet.
    Connection and server errors are raised, so they aren't mistaken for an empty registry."""
    try:
        return MlflowClient().get_model_version_by_alias(model_name, alias)
    except MlflowException as e:
        if e.error_code in NOT_FOUND_CODES:
            return None
        raise


def version_metrics(version: ModelVersion) -> Dict[str, float]:
    return dict(MlflowClient().get_run(version.run_id).data.metrics) if version.run_id else {}


def promote_if_better(
    model_name: str,
    version: str,
    metric: str = GATE_METRIC,
    min_improvement: float = 0.0,
) -> Tuple[bool, str]:
    """
    Approval gate: move @champion to `version` only if it beats the current champion on `metric`
    """
    client = MlflowClient()
    candidate = client.get_model_version(model_name, str(version))
    candidate_score = version_metrics(candidate).get(metric)
    if candidate_score is None:
        return False, f"version {version} has no '{metric}' metric; not promoted"

    champion = get_alias_version(model_name, CHAMPION)
    if champion is not None and champion.version == candidate.version:
        return False, f"version {version} is already the champion"
    champion_score = version_metrics(champion).get(metric) if champion else None

    if champion_score is not None and candidate_score < champion_score + min_improvement:
        return False, (
            f"version {version} {metric}={candidate_score:.4f} does not beat champion "
            f"v{champion.version} ({champion_score:.4f} + {min_improvement})"
        )

    client.set_registered_model_alias(model_name, CHAMPION, candidate.version)
    client.set_model_version_tag(model_name, candidate.version, "approved_at", datetime.now(timezone.utc).isoformat())
    previous = f"v{champion.version} ({champion_score})" if champion else "none"
    return True, f"promoted v{candidate.version} ({metric}={candidate_score:.4f}) to @{CHAMPION}; previous: {previous}"


def bootstrap(model_dir: Path, model_name: str, experiment: str) -> Tuple[ModelVersion, bool]:
    """
    Make sure a champion exists: register the bundled, already-trained model as version 1.
    Idempotent; returns (champion version, created now).
    """
    existing = get_alias_version(model_name, CHAMPION)
    if existing is not None:
        return existing, False

    config = read_model_config(model_dir)
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name="bundled-baseline"):
        mlflow.set_tags({"source": "bundled", "dataset": config.get("training_dataset", "marine_diverse")})
        mlflow.log_params({
            k: config[k]
            for k in ("num_classes", "embedding_dim", "hidden_dim", "num_conv_layers", "dropout", "max_sequence_length")
            if k in config
        })
        if "best_accuracy" in config:
            # Validation accuracy recorded when the bundled weights were trained
            mlflow.log_metric(GATE_METRIC, float(config["best_accuracy"]))
        if "best_epoch" in config:
            mlflow.log_metric("best_epoch", float(config["best_epoch"]))
        version = log_model_version(model_dir, model_name, alias=CHAMPION)
    return version, True


def load_registered(model_name: str, alias: str):
    """
    Load models:/<name>@<alias> and return (ModelLoader, serving info)
    """
    uri = f"models:/{model_name}@{alias}"
    version = get_alias_version(model_name, alias)
    if version is None:
        raise LookupError(f"No model version has alias @{alias} in registered model '{model_name}'")

    pyfunc_model = mlflow.pyfunc.load_model(uri)
    loader = pyfunc_model.unwrap_python_model().loader
    return loader, {
        "source": "mlflow",
        "uri": uri,
        "name": model_name,
        "alias": alias,
        "version": str(version.version),
        "run_id": version.run_id,
        "metrics": version_metrics(version),
        "tracking_uri": mlflow.get_tracking_uri(),
    }
