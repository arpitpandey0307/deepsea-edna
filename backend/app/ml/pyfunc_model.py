"""
MLflow pyfunc wrapper for the marine eDNA classifier.

Logged with MLflow "models from code": MLflow stores this file next to the model
artifacts and executes it on load (no pickled objects). It needs the `app`
package importable at load time, which is true for the API image and for
`backend/` checkouts.

    model = mlflow.pyfunc.load_model("models:/marine-edna-classifier@champion")
    model.predict(pd.DataFrame({"sequence": ["ACGT..."]}))
"""
import pandas as pd
from mlflow.models import set_model
from mlflow.pyfunc import PythonModel


class MarineEDNAModel(PythonModel):
    """Wraps the same ModelLoader the API uses, so MLflow and API predictions are identical"""

    def load_context(self, context):
        from app.services.model_loader import ModelLoader

        self.loader = ModelLoader()
        self.loader.load_from_files(
            config_file=context.artifacts["model_config"],
            taxonomy_file=context.artifacts["taxonomy_map"],
            weights_file=context.artifacts["weights"],
        )

    def predict(self, context, model_input: pd.DataFrame, params=None) -> pd.DataFrame:
        """DataFrame with a `sequence` column -> taxon, confidence and novelty flag per row"""
        sequences = model_input["sequence"].astype(str).tolist()
        return pd.DataFrame(
            [
                {
                    "predicted_class": p["taxon_name"],
                    "confidence": p["confidence"],
                    "is_novel": p["is_novel"],
                }
                for p in self.loader.predict(sequences)
            ]
        )


set_model(MarineEDNAModel())
