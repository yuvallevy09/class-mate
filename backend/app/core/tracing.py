"""MLflow-based tracing for DSPy modules (dev-only observability).

Shared by the FastAPI app (`app.main`) and ad-hoc scripts (`scripts/`), so a
replay script records traces into the same store the running server uses.
"""

from __future__ import annotations

import logging
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]  # backend/


def setup_dspy_tracing() -> None:
    """Record every DSPy module/LM call as an MLflow trace (browse with `mlflow ui`).

    Traces are written to backend/mlflow.db regardless of the process cwd (uvicorn
    is sometimes started from the repo root); view them with
    `mlflow ui --backend-store-uri sqlite:///mlflow.db` from backend/. `mlflow` is a
    dev-only dependency, so a production install without it just logs and moves on.
    """
    try:
        import mlflow
    except ImportError:
        logging.getLogger(__name__).warning(
            "DSPY_TRACING_ENABLED is set but mlflow is not installed; "
            "run `uv sync` in backend/ to install dev dependencies."
        )
        return

    mlflow.set_tracking_uri(f"sqlite:///{_BACKEND_ROOT / 'mlflow.db'}")
    mlflow.set_experiment("classmate-dspy")
    mlflow.dspy.autolog()
