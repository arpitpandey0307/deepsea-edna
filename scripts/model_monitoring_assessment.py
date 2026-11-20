"""
Model monitoring and retraining assessment for the supplied churn dataset.

The workflow loads incoming_batch.csv, creates a reproducible baseline
model from 80% of the supplied records, evaluates that baseline on a
held-out 20% incoming batch, retrains a candidate on all records, and
records a promotion decision. It does not overwrite the baseline.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    precision_score,
    recall_score,
    f1_score,
)
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler


REQUIRED_COLUMNS = [
    "customer_id",
    "tenure_months",
    "monthly_charges",
    "support_tickets_last_90d",
    "contract_type",
    "payment_method",
    "internet_service",
    "churn",
]


def validate_data(df: pd.DataFrame) -> dict:
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing_columns:
        raise ValueError(f"Missing required columns: {missing_columns}")

    missing_values = df[REQUIRED_COLUMNS].isna().sum()
    duplicate_ids = int(df["customer_id"].duplicated().sum())
    if duplicate_ids:
        raise ValueError(f"Duplicate customer_id records found: {duplicate_ids}")

    target_counts = df["churn"].value_counts(dropna=False).to_dict()
    if df["churn"].isna().any():
        raise ValueError("Target column contains missing values")

    return {
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "missing_values": {
            column: int(count) for column, count in missing_values.items() if count > 0
        },
        "duplicate_customer_ids": duplicate_ids,
        "target_distribution": target_counts,
    }


def build_pipeline(X: pd.DataFrame) -> Pipeline:
    categorical = X.select_dtypes(include=["object", "category"]).columns.tolist()
    numeric = X.select_dtypes(include=["number"]).columns.tolist()

    categorical_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="most_frequent")),
        ("encoder", OneHotEncoder(handle_unknown="ignore")),
    ])
    numeric_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
    ])

    preprocessing = ColumnTransformer([
        ("categorical", categorical_pipeline, categorical),
        ("numeric", numeric_pipeline, numeric),
    ])
    return Pipeline([
        ("preprocessing", preprocessing),
        ("classifier", LogisticRegression(max_iter=1000, random_state=42)),
    ])


def evaluate(model: Pipeline, X: pd.DataFrame, y: pd.Series) -> dict:
    predictions = model.predict(X)
    return {
        "accuracy": float(accuracy_score(y, predictions)),
        "precision": float(precision_score(y, predictions, pos_label="yes", zero_division=0)),
        "recall": float(recall_score(y, predictions, pos_label="yes", zero_division=0)),
        "f1": float(f1_score(y, predictions, pos_label="yes", zero_division=0)),
    }


def log_mlflow(run_name: str, metrics: dict, params: dict, candidate_path: Path) -> None:
    try:
        import mlflow
        import mlflow.sklearn
    except ImportError:
        return

    mlflow.set_experiment("deepsea-churn-monitoring")
    with mlflow.start_run(run_name=run_name):
        mlflow.log_params(params)
        mlflow.log_metrics(metrics)
        mlflow.log_artifact(str(candidate_path))


def run(dataset_path: Path, output_dir: Path) -> dict:
    df = pd.read_csv(dataset_path)
    data_validation = validate_data(df)

    X = df.drop(columns=["churn", "customer_id"])
    y = df["churn"].astype(str)

    X_existing, X_incoming, y_existing, y_incoming = train_test_split(
        X,
        y,
        test_size=0.20,
        random_state=42,
        stratify=y,
    )

    baseline = build_pipeline(X_existing)
    baseline.fit(X_existing, y_existing)

    output_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = output_dir / "champion.joblib"
    candidate_path = output_dir / "candidate.joblib"
    joblib.dump(baseline, baseline_path)

    champion_metrics = evaluate(baseline, X_incoming, y_incoming)

    candidate = build_pipeline(pd.concat([X_existing, X_incoming]))
    candidate.fit(pd.concat([X_existing, X_incoming]), pd.concat([y_existing, y_incoming]))
    joblib.dump(candidate, candidate_path)

    candidate_metrics = evaluate(candidate, X_incoming, y_incoming)

    quality_metric = "f1"
    improvement = candidate_metrics[quality_metric] - champion_metrics[quality_metric]
    eligible = candidate_metrics[quality_metric] >= champion_metrics[quality_metric]
    decision = "eligible_for_promotion" if eligible else "retain_champion"

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset_path),
        "task_a_data_validation": data_validation,
        "incoming_batch": {
            "rows": int(len(X_incoming)),
            "target_distribution": y_incoming.value_counts().to_dict(),
        },
        "existing_training_split": {
            "rows": int(len(X_existing)),
        },
        "task_b_existing_model": {
            "artifact": str(baseline_path),
            "metrics": champion_metrics,
        },
        "task_c_candidate_model": {
            "artifact": str(candidate_path),
            "metrics": candidate_metrics,
            "training_rows": int(len(X_existing) + len(X_incoming)),
        },
        "task_d_quality_gate": {
            "selected_metric": quality_metric,
            "reason": "F1 balances precision and recall for the imbalanced churn outcome.",
            "required_rule": "candidate F1 must be greater than or equal to champion F1",
            "champion_f1": champion_metrics["f1"],
            "candidate_f1": candidate_metrics["f1"],
            "improvement": improvement,
            "decision": decision,
            "champion_overwritten": False,
        },
    }

    report_path = output_dir / "evaluation_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    comparison = pd.DataFrame([
        {"model": "champion", **champion_metrics},
        {"model": "candidate", **candidate_metrics},
    ])
    comparison.to_csv(output_dir / "evaluation_report.csv", index=False)

    log_mlflow(
        "churn-monitoring-retraining",
        {"champion_f1": champion_metrics["f1"], "candidate_f1": candidate_metrics["f1"]},
        {"quality_metric": quality_metric, "incoming_rows": len(X_incoming)},
        candidate_path,
    )

    print(json.dumps(report, indent=2))
    print(f"\nEvaluation report: {report_path}")
    print(f"Candidate artifact: {candidate_path}")
    print(f"Champion artifact retained: {baseline_path}")
    print(f"Quality-gate decision: {decision}")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("incoming_batch.csv"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("ml_models/churn_assessment"),
    )
    args = parser.parse_args()
    run(args.dataset, args.output)
