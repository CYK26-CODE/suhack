"""Risk Prediction Agent — service layer.

Builds feature matrix from analysis + complexity, runs IsolationForest,
normalises scores to [0,1], and classifies into risk levels.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import structlog
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from app.core.config import get_settings
from app.core.context_store import ContextStore
from app.core.exceptions import RiskError
from app.core.schemas import (
    RiskLevel,
    RiskRecord,
    RiskResult,
    RunContext,
    StageStatus,
)
from app.modules.risk.model_store import save_model

log = structlog.get_logger(__name__)
settings = get_settings()

FEATURES = [
    "total_churn",
    "commit_count",
    "contributors",
    "complexity_adj",
    "mi_inverted",
]


def _score_to_level(score: float) -> RiskLevel:
    """Map a normalised score to a risk level."""
    threshold = settings.risk_threshold
    if score >= threshold:
        return RiskLevel.HIGH
    if score >= 0.4:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def build_feature_matrix(
    ctx: RunContext,
) -> tuple[pd.DataFrame, list[str]]:
    """Join analysis + complexity into a feature DataFrame."""
    analysis_map = {r.file: r for r in ctx.analysis}
    rows: list[dict] = []
    files: list[str] = []

    for rec in ctx.complexity:
        an = analysis_map.get(rec.file)
        if an is None:
            continue
        if rec.parse_error:
            continue  # handled separately as guaranteed HIGH

        rows.append(
            {
                "total_churn": an.total_churn,
                "commit_count": an.commit_count,
                "contributors": an.contributors,
                "complexity_adj": max(rec.complexity, 0.0),
                "mi_inverted": 100.0 - max(rec.maintainability, 0.0),
            }
        )
        files.append(rec.file)

    return pd.DataFrame(rows, columns=FEATURES), files


def run_isolation_forest(
    df: pd.DataFrame,
) -> tuple[np.ndarray, IsolationForest, StandardScaler]:
    """Fit IsolationForest and return normalised scores."""
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(df.values)

    contamination = "auto" if len(df) >= 10 else 0.1

    model = IsolationForest(
        n_estimators=200,
        contamination=contamination,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_scaled)

    raw = model.score_samples(X_scaled)
    inverted = -raw
    mn, mx = inverted.min(), inverted.max()

    if mx > mn:
        normalised = (inverted - mn) / (mx - mn)
    else:
        log.warning("all_scores_identical", count=len(df))
        normalised = np.zeros(len(df))

    return normalised, model, scaler


async def run_risk(ctx: RunContext, store: ContextStore) -> RiskResult:
    """Execute risk prediction stage and checkpoint results."""
    if not ctx.complexity:
        raise RiskError("complexity stage must run before risk prediction")

    log.info("risk_started", run_id=ctx.run_id)
    ctx.mark_stage("risk", StageStatus.RUNNING)

    df, scored_files = build_feature_matrix(ctx)
    parse_error_files = [r.file for r in ctx.complexity if r.parse_error]

    records: list[RiskRecord] = []

    if len(df) > 0:
        scores, model, scaler = run_isolation_forest(df)
        for file, score in zip(scored_files, scores):
            records.append(
                RiskRecord(
                    file=file,
                    risk_score=round(float(score), 4),
                    risk_level=_score_to_level(float(score)),
                )
            )
        save_model(model, scaler, ctx.run_id)

    # Parse error files are always HIGH risk
    for file in parse_error_files:
        records.append(
            RiskRecord(file=file, risk_score=1.0, risk_level=RiskLevel.HIGH)
        )

    ctx.risk = records
    ctx.mark_stage("risk", StageStatus.COMPLETE)
    await store.set(ctx.run_id, ctx)  # checkpoint

    high_count = sum(1 for r in records if r.risk_level == RiskLevel.HIGH)
    log.info("risk_complete", run_id=ctx.run_id, total=len(records), high=high_count)

    return RiskResult(
        run_id=ctx.run_id,
        risk=records,
        high_risk_count=high_count,
        model_version=ctx.run_id,
    )
