"""
MLflow integration tests: registry bootstrap, champion loading, approval gate and
API serving/reload. Uses a throwaway SQLite-backed MLflow store, so no server is needed.
"""
from pathlib import Path

import mlflow
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.ml import cli, registry
from app.services import serving

MODEL = "test-marine-classifier"
EXPERIMENT = "test-marine-experiment"
MODEL_DIR = Path(settings.MODEL_DIR) / settings.DEFAULT_MODEL
SEQ = "ATGCGTACGTTAGCCTAGGCTAACGTTAGCATCGATCGGCTAGCTAGGATCCGATCGATTACGGCTAGCTAACG" * 3
PASSWORD = "Str0ng-passw0rd"


@pytest.fixture(scope="module")
def mlflow_store(tmp_path_factory):
    root = tmp_path_factory.mktemp("mlflow")
    uri = f"sqlite:///{(root / 'mlflow.db').as_posix()}"
    registry.configure(uri)
    mlflow.create_experiment(EXPERIMENT, artifact_location=(root / "artifacts").as_uri())
    registry.bootstrap(MODEL_DIR, MODEL, EXPERIMENT)
    yield uri
    mlflow.set_tracking_uri("")


@pytest.fixture
def mlflow_settings(mlflow_store, monkeypatch):
    """Point the API's settings at the test MLflow store"""
    monkeypatch.setattr(settings, "MLFLOW_TRACKING_URI", mlflow_store)
    monkeypatch.setattr(settings, "MLFLOW_MODEL_NAME", MODEL)
    monkeypatch.setattr(settings, "MLFLOW_MODEL_ALIAS", registry.CHAMPION)
    monkeypatch.setattr(settings, "MODEL_SOURCE", "mlflow")
    return mlflow_store


def log_candidate(val_accuracy: float):
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run():
        mlflow.log_metric(registry.GATE_METRIC, val_accuracy)
        return registry.log_model_version(MODEL_DIR, MODEL, alias=registry.CHALLENGER)


def test_bootstrap_registers_champion_once(mlflow_store):
    champion = registry.get_alias_version(MODEL, registry.CHAMPION)
    assert champion is not None
    assert registry.version_metrics(champion)[registry.GATE_METRIC] == pytest.approx(0.9418)

    again, created = registry.bootstrap(MODEL_DIR, MODEL, EXPERIMENT)
    assert created is False and again.version == champion.version


def test_champion_from_mlflow_matches_local_model(mlflow_store):
    loader, info = registry.load_registered(MODEL, registry.CHAMPION)
    assert info["source"] == "mlflow" and info["uri"] == f"models:/{MODEL}@champion"

    local_loader, _ = serving.load_local()
    for remote, local in zip(loader.predict([SEQ, SEQ[::-1]]), local_loader.predict([SEQ, SEQ[::-1]])):
        assert remote["taxon_name"] == local["taxon_name"]
        assert remote["confidence"] == pytest.approx(local["confidence"], abs=1e-6)


def test_standard_pyfunc_interface(mlflow_store):
    model = mlflow.pyfunc.load_model(f"models:/{MODEL}@{registry.CHAMPION}")
    out = model.predict(pd.DataFrame({"sequence": [SEQ, SEQ[::-1]]}))
    assert list(out.columns) == ["predicted_class", "confidence", "is_novel"]
    assert len(out) == 2 and out["confidence"].between(0, 1).all()


def test_approval_gate(mlflow_store):
    champion = registry.get_alias_version(MODEL, registry.CHAMPION).version

    worse = log_candidate(0.50)
    promoted, message = registry.promote_if_better(MODEL, worse.version)
    assert promoted is False and "does not beat" in message
    assert registry.get_alias_version(MODEL, registry.CHAMPION).version == champion

    better = log_candidate(0.99)
    promoted, _ = registry.promote_if_better(MODEL, better.version)
    assert promoted is True
    assert registry.get_alias_version(MODEL, registry.CHAMPION).version == better.version
    assert registry.get_alias_version(MODEL, registry.CHALLENGER).version == better.version


def test_model_source_modes(mlflow_settings, monkeypatch):
    _, info = serving.load_serving_model()
    assert info["source"] == "mlflow"

    monkeypatch.setattr(settings, "MLFLOW_MODEL_NAME", "does-not-exist")
    with pytest.raises(RuntimeError):
        serving.load_serving_model()  # mlflow mode fails fast

    monkeypatch.setattr(settings, "MODEL_SOURCE", "auto")
    _, info = serving.load_serving_model()
    assert info["source"] == "local"  # auto falls back to the bundled model


def test_cli_info_and_require(mlflow_store, capsys):
    assert cli.main(["--tracking-uri", mlflow_store, "--model-name", MODEL, "info"]) == 0
    assert "@champion: v" in capsys.readouterr().out
    worse = log_candidate(0.10)
    args = ["--tracking-uri", mlflow_store, "--model-name", MODEL, "promote", "--version", str(worse.version), "--require"]
    assert cli.main(args) == 1


def test_api_serves_champion_and_hot_reloads(mlflow_settings):
    from app.main import app

    with TestClient(app) as client:
        client.post("/auth/register", json={
            "username": "mlops_admin", "email": "admin@example.com", "full_name": "MLOps Admin", "password": PASSWORD,
        })  # may already exist from the integration suite
        tokens = client.post("/auth/token", data={"username": "admin@example.com", "password": PASSWORD}).json()
        auth = {"Authorization": f"Bearer {tokens['access_token']}"}

        served = client.get("/api/model/serving", headers=auth).json()
        champion = str(registry.get_alias_version(MODEL, registry.CHAMPION).version)  # API reports strings
        assert served["source"] == "mlflow" and served["version"] == champion
        assert client.post("/api/predict", json={"sequence": SEQ}, headers=auth).status_code == 200

        # Promote a new champion in MLflow, then reload without restarting the API
        newer = log_candidate(0.999)
        assert registry.promote_if_better(MODEL, newer.version)[0]
        reloaded = client.post("/api/model/reload", headers=auth).json()
        assert reloaded["previous_version"] == champion and reloaded["version"] == str(newer.version)

        current = client.get("/versions/current/info", headers=auth).json()
        assert current["version_name"] == f"{MODEL} v{newer.version}"
        prediction = client.post("/api/predict", json={"sequence": SEQ}, headers=auth).json()
        assert prediction["cached"] is False  # cache keys are per model version
