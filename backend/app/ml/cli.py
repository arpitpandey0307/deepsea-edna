"""
MLflow registry command line (run from backend/ or inside the API image)

    python -m app.ml.cli bootstrap              # register the bundled model as v1 @champion (idempotent)
    python -m app.ml.cli promote --version 3    # approval gate: champion only if it beats the current one
    python -m app.ml.cli info                   # show the current champion and challenger

Uses MLFLOW_TRACKING_URI, MLFLOW_MODEL_NAME and MLFLOW_EXPERIMENT from the environment/.env.
"""
import argparse
import logging
import sys
from pathlib import Path

from app.core.config import settings
from app.ml import registry


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tracking-uri", default=settings.MLFLOW_TRACKING_URI)
    parser.add_argument("--model-name", default=settings.MLFLOW_MODEL_NAME)
    sub = parser.add_subparsers(dest="command", required=True)

    boot = sub.add_parser("bootstrap", help="register the bundled model if no champion exists")
    boot.add_argument("--model-dir", default=str(Path(settings.MODEL_DIR) / settings.DEFAULT_MODEL))
    boot.add_argument("--experiment", default=settings.MLFLOW_EXPERIMENT)

    promote = sub.add_parser("promote", help="move @champion to a version if it beats the current champion")
    promote.add_argument("--version", required=True)
    promote.add_argument("--metric", default=registry.GATE_METRIC)
    promote.add_argument("--min-improvement", type=float, default=0.0)
    promote.add_argument("--require", action="store_true", help="exit 1 if the version is not promoted")

    sub.add_parser("info", help="show champion and challenger versions")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    registry.configure(args.tracking_uri)

    if args.command == "bootstrap":
        version, created = registry.bootstrap(Path(args.model_dir), args.model_name, args.experiment)
        state = "registered" if created else "already registered"
        print(f"{args.model_name} v{version.version} @{registry.CHAMPION} ({state})")
        return 0

    if args.command == "promote":
        promoted, message = registry.promote_if_better(args.model_name, args.version, args.metric, args.min_improvement)
        print(message)
        return 1 if (args.require and not promoted) else 0

    for alias in (registry.CHAMPION, registry.CHALLENGER):
        version = registry.get_alias_version(args.model_name, alias)
        if version is None:
            print(f"@{alias}: none")
        else:
            metrics = registry.version_metrics(version)
            print(f"@{alias}: v{version.version} run={version.run_id} {registry.GATE_METRIC}={metrics.get(registry.GATE_METRIC)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
