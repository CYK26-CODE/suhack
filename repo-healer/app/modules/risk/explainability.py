"""Risk Explainability Module.

Generates per-file explanations of why the IsolationForest model flagged
files as risky, using z-score analysis and feature contribution weights.
"""

from __future__ import annotations

import numpy as np
import structlog

from app.core.config import get_settings
from app.core.schemas import (
    ExplainabilityReport,
    FeatureContribution,
    RiskExplanation,
    RiskLevel,
    RunContext,
)

log = structlog.get_logger(__name__)
settings = get_settings()

# Human-readable labels for each feature
FEATURE_LABELS = {
    "total_churn": "Code Churn",
    "commit_count": "Commit Frequency",
    "contributors": "Contributor Count",
    "complexity_adj": "Cyclomatic Complexity",
    "mi_inverted": "Maintainability Deficit",
}

FEATURE_KEYS = list(FEATURE_LABELS.keys())

METHODOLOGY = (
    "Risk scores are computed using an IsolationForest anomaly detection model "
    "trained on 5 features: code churn (total lines added+deleted), commit frequency, "
    "contributor count, cyclomatic complexity, and inverted maintainability index. "
    "Files with scores >= {threshold} are flagged HIGH risk. Feature contributions "
    "are derived from z-score deviations -- features furthest from the mean contribute "
    "most to a file's anomaly status."
)


def _severity_label(z: float) -> str:
    """Map absolute z-score to a severity label."""
    az = abs(z)
    if az >= 2.5:
        return "critical"
    if az >= 1.5:
        return "high"
    if az >= 0.75:
        return "elevated"
    return "normal"


def _human_reason(label: str, raw: float, z: float) -> str:
    """Generate a plain-English sentence for one feature."""
    sev = _severity_label(z)
    direction = "above" if z > 0 else "below"

    if sev == "normal":
        return f"{label}: {raw:.0f} (within normal range)"

    sigma = f"{abs(z):.1f}x"
    if label == "Code Churn":
        return f"High code churn: {raw:.0f} lines changed ({sigma} {direction} average)"
    if label == "Commit Frequency":
        return f"Frequent commits: {raw:.0f} commits ({sigma} {direction} average)"
    if label == "Contributor Count":
        return f"Many contributors: {raw:.0f} authors ({sigma} {direction} average)"
    if label == "Cyclomatic Complexity":
        return f"Complex code: avg complexity {raw:.1f} ({sigma} {direction} average)"
    if label == "Maintainability Deficit":
        return f"Low maintainability: deficit score {raw:.1f} ({sigma} {direction} average)"

    return f"{label}: {raw:.1f} ({sigma} {direction} average)"


def generate_report(ctx: RunContext) -> ExplainabilityReport:
    """Build an explainability report from the pipeline's RunContext.

    Re-computes z-scores from the analysis + complexity data to explain
    each risk record's score.
    """
    # Build feature matrix matching the risk service logic
    analysis_map = {r.file: r for r in ctx.analysis}
    complexity_map = {r.file: r for r in ctx.complexity}

    # Collect raw feature rows for all scored files
    rows: list[dict[str, float]] = []
    files: list[str] = []

    for rec in ctx.complexity:
        an = analysis_map.get(rec.file)
        if an is None or rec.parse_error:
            continue
        rows.append({
            "total_churn": float(an.total_churn),
            "commit_count": float(an.commit_count),
            "contributors": float(an.contributors),
            "complexity_adj": max(float(rec.complexity), 0.0),
            "mi_inverted": 100.0 - max(float(rec.maintainability), 0.0),
        })
        files.append(rec.file)

    # Compute means and stds for z-scores
    if not rows:
        return ExplainabilityReport(
            run_id=ctx.run_id,
            repo_url=ctx.repo_url,
            total_files=len(ctx.analysis),
            high_risk_count=0,
            risk_threshold=settings.risk_threshold,
            methodology=METHODOLOGY.format(threshold=settings.risk_threshold),
            explanations=[],
        )

    arr = np.array([[r[k] for k in FEATURE_KEYS] for r in rows])
    means = arr.mean(axis=0)
    stds = arr.std(axis=0)
    stds[stds == 0] = 1.0  # avoid division by zero

    # Build risk lookup
    risk_map = {r.file: r for r in ctx.risk}

    explanations: list[RiskExplanation] = []

    for idx, file in enumerate(files):
        risk_rec = risk_map.get(file)
        if risk_rec is None:
            continue

        raw_row = rows[idx]
        z_scores = (arr[idx] - means) / stds

        # Compute contribution weights (higher |z| = higher contribution)
        abs_z = np.abs(z_scores)
        total_z = abs_z.sum()
        contributions = abs_z / total_z if total_z > 0 else np.zeros(len(FEATURE_KEYS))

        feature_contribs: list[FeatureContribution] = []
        reasons: list[str] = []

        for i, key in enumerate(FEATURE_KEYS):
            label = FEATURE_LABELS[key]
            z = float(z_scores[i])
            raw = raw_row[key]
            contrib = float(contributions[i])
            sev = _severity_label(z)

            feature_contribs.append(FeatureContribution(
                name=key,
                label=label,
                raw_value=round(raw, 2),
                z_score=round(z, 2),
                contribution=round(contrib, 3),
                severity=sev,
            ))

            if sev != "normal":
                reasons.append(_human_reason(label, raw, z))

        # Sort by contribution descending
        feature_contribs.sort(key=lambda fc: fc.contribution, reverse=True)
        top_driver = feature_contribs[0].label if feature_contribs else "Unknown"

        if not reasons:
            reasons.append("Flagged due to combined deviation across multiple features")

        explanations.append(RiskExplanation(
            file=file,
            risk_score=risk_rec.risk_score,
            risk_level=risk_rec.risk_level,
            reasons=reasons,
            feature_contributions=feature_contribs,
            top_driver=top_driver,
        ))

    # Add parse-error files
    parse_error_files = {r.file for r in ctx.complexity if r.parse_error}
    for file in parse_error_files:
        risk_rec = risk_map.get(file)
        if risk_rec is None:
            continue
        explanations.append(RiskExplanation(
            file=file,
            risk_score=1.0,
            risk_level=RiskLevel.HIGH,
            reasons=["File has syntax errors and could not be parsed - automatically flagged as HIGH risk"],
            feature_contributions=[],
            top_driver="Parse Error",
        ))

    # Sort: HIGH risk first, then by score descending
    level_order = {RiskLevel.HIGH: 0, RiskLevel.MEDIUM: 1, RiskLevel.LOW: 2}
    explanations.sort(key=lambda e: (level_order.get(e.risk_level, 9), -e.risk_score))

    high_count = sum(1 for e in explanations if e.risk_level == RiskLevel.HIGH)

    return ExplainabilityReport(
        run_id=ctx.run_id,
        repo_url=ctx.repo_url,
        total_files=len(ctx.analysis),
        high_risk_count=high_count,
        risk_threshold=settings.risk_threshold,
        methodology=METHODOLOGY.format(threshold=settings.risk_threshold),
        explanations=explanations,
    )
