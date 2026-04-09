"""Risk Prediction Agent — model persistence.

Serialise/load trained IsolationForest models with joblib.
"""

from __future__ import annotations

import pathlib
from typing import Any

import joblib

MODEL_DIR = pathlib.Path("models")


def save_model(model: Any, scaler: Any, run_id: str) -> None:
    """Persist trained model and scaler to disk."""
    MODEL_DIR.mkdir(exist_ok=True)
    joblib.dump(
        {"model": model, "scaler": scaler},
        MODEL_DIR / f"if_{run_id}.joblib",
    )


def load_latest_model() -> tuple | None:
    """Load the most recently saved model, or None if no models exist."""
    if not MODEL_DIR.exists():
        return None
    models = sorted(MODEL_DIR.glob("if_*.joblib"))
    if not models:
        return None
    data = joblib.load(models[-1])
    return data["model"], data["scaler"]
