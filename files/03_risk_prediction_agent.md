# Module 03 — Risk Prediction Agent

## Purpose

The Risk Prediction Agent is the **third stage** of the pipeline. It merges the file-level
metadata from the Analyzer with the complexity scores from the Complexity Agent into a feature
matrix, then uses scikit-learn's **Isolation Forest** to assign a normalised risk score to each
file. Files scoring above the `RISK_THRESHOLD` (default: 0.7) are flagged for healing.

---

## Tech Stack

| Dependency          | Version | Role                                                        |
|---------------------|---------|-------------------------------------------------------------|
| FastAPI             | ≥0.111  | HTTP router                                                 |
| Pydantic v2         | ≥2.7    | `RiskRecord`, `RiskResult` schemas                          |
| scikit-learn        | ≥1.4    | `IsolationForest`, `StandardScaler`                         |
| numpy               | ≥1.26   | Feature matrix construction                                 |
| joblib              | ≥1.3    | Serialise/load trained model for incremental runs           |
| pandas              | ≥2.2    | Feature DataFrame assembly                                  |
| structlog           | ≥24.1   | Structured logging                                          |
| pytest              | ≥8.0    | Test runner                                                 |
| pytest-mock         | ≥3.12   | Mock sklearn calls                                          |

---

## API Endpoint

### `POST /api/v1/predict/risk`

**Request Body:**

```json
{ "run_id": "20241120-143200" }
```

**Success Response — `200 OK`:**

```json
{
  "run_id": "20241120-143200",
  "risk": [
    {
      "file": "src/utils.py",
      "risk_score": 0.82,
      "risk_level": "HIGH",
      "features": {
        "total_churn": 142,
        "commit_count": 17,
        "contributors": 3,
        "complexity": 8.4,
        "maintainability": 52.3
      }
    }
  ],
  "high_risk_count": 3,
  "model_version": "20241120-143200"
}
```

**Error Responses:**

| Status | Condition                                                        |
|--------|------------------------------------------------------------------|
| 404    | `run_id` not found                                               |
| 424    | `RunContext.complexity` is empty (complexity stage not yet run)  |
| 422    | Malformed body                                                   |

---

## Feature Engineering

The feature matrix is built by joining `RunContext.analysis` and `RunContext.complexity` on
the `file` field:

| Feature           | Source           | Notes                                          |
|-------------------|------------------|------------------------------------------------|
| `total_churn`     | Analyzer         | Raw integer, scaled by StandardScaler          |
| `commit_count`    | Analyzer         | Raw integer, scaled                            |
| `contributors`    | Analyzer         | Raw integer, scaled                            |
| `complexity`      | Complexity Agent | Average cyclomatic; `-1.0` files treated as high|
| `maintainability` | Complexity Agent | Inverted (100 - MI) so higher = riskier        |

`maintainability` is inverted before scaling because the Isolation Forest treats high values as
potential anomalies. Since low MI means hard-to-maintain code, inverting makes the feature
directionally consistent with the others (higher = worse).

---

## Model: Isolation Forest

### Why Isolation Forest?

There are no labelled "buggy file" datasets for arbitrary repositories. Isolation Forest is
unsupervised — it identifies files that are statistically unusual compared to the rest of the
repository without needing labelled examples of bugs.

This makes it the correct tool for this problem. **It does not detect bugs.** It detects files
that deviate significantly from the repository's norm on churn, complexity, and contributor
dimensions. These files are empirically more likely to contain defects, but the risk score is
a heuristic, not a proof.

### Score Normalisation

sklearn's `IsolationForest.score_samples()` returns raw anomaly scores in the range `(-∞, 0]`
in practice, typically in `[-1, 0]`. The sign convention is: **more negative = more anomalous**.
`decision_function()` shifts these by a learned offset and also produces negative values for
anomalies.

We normalise to `[0, 1]` as follows:

```python
raw_scores = model.score_samples(X_scaled)      # shape (n_files,), range ≈ [-1, 0]
# sklearn raw: -0.5 mean anomaly, closer to -1 = more anomalous
# invert: anomalous → high score
inverted = -raw_scores                           # range ≈ [0, 1]
# min-max normalise to ensure output is always in [0, 1]
min_s, max_s = inverted.min(), inverted.max()
if max_s > min_s:
    normalised = (inverted - min_s) / (max_s - min_s)
else:
    normalised = np.zeros_like(inverted)
```

This normalisation is min-max **within the current batch**. A score of `0.82` means "this file
is more anomalous than 82% of files in this repository run," not an absolute bug probability.

### Risk Level Thresholds

| normalised_score | risk_level | Interpretation                        |
|------------------|------------|---------------------------------------|
| ≥ 0.70           | HIGH       | Flagged for healing                   |
| 0.40 – 0.69      | MEDIUM     | Logged; not healed in current version |
| < 0.40           | LOW        | Normal for this repository            |

Thresholds are configurable via `RISK_THRESHOLD` (controls the HIGH boundary only).

### Parse-Error Files

Files with `complexity == -1.0` (parse errors from the Complexity Agent) are **not** fed into
the Isolation Forest. They are assigned `risk_score = 1.0, risk_level = HIGH` as a conservative
fallback. This ensures that unparseable files are always reviewed, not silently skipped.

### Model Persistence

The trained model is serialised to `models/isolation_forest_{run_id}.joblib` after each run.
For subsequent runs on the same repository, the previous model can be loaded and used as a
warm start via `IsolationForest(warm_start=True)`. This is optional and controlled by
`MODEL_WARM_START` env var (default: `False`).

```python
# model_store.py
import joblib
import pathlib

MODEL_DIR = pathlib.Path("models")

def save_model(model, scaler, run_id: str) -> None:
    MODEL_DIR.mkdir(exist_ok=True)
    joblib.dump({"model": model, "scaler": scaler}, MODEL_DIR / f"if_{run_id}.joblib")

def load_latest_model() -> tuple | None:
    models = sorted(MODEL_DIR.glob("if_*.joblib"))
    if not models:
        return None
    return joblib.load(models[-1])
```

---

## Service Implementation

```python
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

FEATURES = ["total_churn", "commit_count", "contributors", "complexity_adj", "mi_inverted"]

def build_feature_matrix(ctx: RunContext) -> tuple[pd.DataFrame, list[str]]:
    analysis_map = {r.file: r for r in ctx.analysis}
    rows, files = [], []
    for rec in ctx.complexity:
        an = analysis_map.get(rec.file)
        if an is None:
            continue
        if rec.parse_error:
            continue  # handled separately as guaranteed HIGH
        rows.append({
            "total_churn":    an.total_churn,
            "commit_count":   an.commit_count,
            "contributors":   an.contributors,
            "complexity_adj": max(rec.complexity, 0.0),
            "mi_inverted":    100.0 - max(rec.maintainability, 0.0),
        })
        files.append(rec.file)
    return pd.DataFrame(rows, columns=FEATURES), files

def run_isolation_forest(df: pd.DataFrame) -> np.ndarray:
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(df.values)
    model = IsolationForest(
        n_estimators=200,
        contamination="auto",
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_scaled)
    raw = model.score_samples(X_scaled)
    inverted = -raw
    mn, mx = inverted.min(), inverted.max()
    if mx > mn:
        return (inverted - mn) / (mx - mn), model, scaler
    return np.zeros(len(df)), model, scaler
```

---

## Context Store Integration

```python
async def run_risk(ctx: RunContext, store: ContextStore) -> RiskResult:
    if not ctx.complexity:
        raise RiskError("complexity stage must run before risk prediction")

    df, scored_files = build_feature_matrix(ctx)
    parse_error_files = [r.file for r in ctx.complexity if r.parse_error]

    records: list[RiskRecord] = []

    if len(df) > 0:
        scores, model, scaler = run_isolation_forest(df)
        for file, score in zip(scored_files, scores):
            records.append(RiskRecord(
                file=file,
                risk_score=round(float(score), 4),
                risk_level=_score_to_level(float(score)),
            ))
        save_model(model, scaler, ctx.run_id)

    for file in parse_error_files:
        records.append(RiskRecord(file=file, risk_score=1.0, risk_level=RiskLevel.HIGH))

    ctx.risk = records
    ctx.stage_flags["risk"] = StageStatus.COMPLETE
    ctx.last_updated = datetime.utcnow()
    await store.set(ctx.run_id, ctx)
    return RiskResult(run_id=ctx.run_id, risk=records, high_risk_count=sum(1 for r in records if r.risk_level == RiskLevel.HIGH))
```

---

## Testing Module: `tests/test_03_risk.py`

```python
import pytest
import numpy as np
import pandas as pd
from app.modules.risk.service import (
    build_feature_matrix, run_isolation_forest, _score_to_level
)
from app.modules.risk.schemas import RiskRecord, RiskLevel

# ── Unit Tests ───────────────────────────────────────────────────────────────

class TestBuildFeatureMatrix:

    def test_returns_correct_columns(self, run_context_with_complexity):
        df, files = build_feature_matrix(run_context_with_complexity)
        assert list(df.columns) == ["total_churn", "commit_count", "contributors",
                                     "complexity_adj", "mi_inverted"]

    def test_parse_error_files_excluded_from_matrix(self, run_context_with_parse_error):
        df, files = build_feature_matrix(run_context_with_parse_error)
        assert not any("broken" in f for f in files)

    def test_file_count_matches_non_error_records(self, run_context_with_complexity):
        df, files = build_feature_matrix(run_context_with_complexity)
        non_error = sum(1 for r in run_context_with_complexity.complexity if not r.parse_error)
        assert len(df) == non_error

    def test_mi_inverted(self, run_context_with_complexity):
        df, _ = build_feature_matrix(run_context_with_complexity)
        # mi_inverted = 100 - maintainability
        for i, rec in enumerate(r for r in run_context_with_complexity.complexity if not r.parse_error):
            expected_inv = 100.0 - max(rec.maintainability, 0.0)
            assert abs(df.iloc[i]["mi_inverted"] - expected_inv) < 0.01

    def test_missing_analysis_record_excluded(self, run_context_complexity_without_analysis):
        df, files = build_feature_matrix(run_context_complexity_without_analysis)
        assert len(files) < len(run_context_complexity_without_analysis.complexity)


class TestRunIsolationForest:

    def test_scores_in_zero_to_one_range(self, sample_feature_df):
        scores, _, _ = run_isolation_forest(sample_feature_df)
        assert np.all(scores >= 0.0)
        assert np.all(scores <= 1.0)

    def test_high_churn_file_scores_higher(self):
        normal = pd.DataFrame([
            {"total_churn": 10, "commit_count": 2, "contributors": 1, "complexity_adj": 3.0, "mi_inverted": 30.0}
            for _ in range(10)
        ])
        anomaly = pd.DataFrame([
            {"total_churn": 5000, "commit_count": 200, "contributors": 30, "complexity_adj": 50.0, "mi_inverted": 90.0}
        ])
        df = pd.concat([normal, anomaly], ignore_index=True)
        scores, _, _ = run_isolation_forest(df)
        assert scores[-1] > scores[:-1].mean()

    def test_single_file_returns_zero_score(self):
        df = pd.DataFrame([
            {"total_churn": 5, "commit_count": 1, "contributors": 1, "complexity_adj": 2.0, "mi_inverted": 20.0}
        ])
        scores, _, _ = run_isolation_forest(df)
        assert len(scores) == 1
        assert scores[0] == 0.0  # single file: min-max normalisation produces 0

    def test_identical_files_all_zero(self):
        df = pd.DataFrame([
            {"total_churn": 10, "commit_count": 2, "contributors": 1, "complexity_adj": 3.0, "mi_inverted": 20.0}
        ] * 5)
        scores, _, _ = run_isolation_forest(df)
        assert np.all(scores == 0.0)

    def test_model_deterministic_with_random_state(self, sample_feature_df):
        s1, _, _ = run_isolation_forest(sample_feature_df)
        s2, _, _ = run_isolation_forest(sample_feature_df)
        np.testing.assert_array_almost_equal(s1, s2)


class TestScoreToLevel:

    @pytest.mark.parametrize("score,expected", [
        (0.75, RiskLevel.HIGH),
        (0.70, RiskLevel.HIGH),
        (0.50, RiskLevel.MEDIUM),
        (0.40, RiskLevel.MEDIUM),
        (0.39, RiskLevel.LOW),
        (0.0,  RiskLevel.LOW),
    ])
    def test_thresholds(self, score, expected):
        assert _score_to_level(score) == expected

    def test_above_one_treated_as_high(self):
        assert _score_to_level(1.01) == RiskLevel.HIGH


class TestParseErrorFiles:

    def test_parse_error_files_get_score_1(self, run_context_with_parse_error, context_store, mocker):
        import asyncio
        from app.modules.risk.service import run_risk
        result = asyncio.get_event_loop().run_until_complete(
            run_risk(run_context_with_parse_error, context_store)
        )
        error_records = [r for r in result.risk if r.risk_score == 1.0]
        assert len(error_records) >= 1

    def test_parse_error_files_risk_level_high(self, run_context_with_parse_error, context_store):
        import asyncio
        from app.modules.risk.service import run_risk
        result = asyncio.get_event_loop().run_until_complete(
            run_risk(run_context_with_parse_error, context_store)
        )
        for r in result.risk:
            if r.risk_score == 1.0:
                assert r.risk_level == RiskLevel.HIGH


# ── Router Integration Tests ─────────────────────────────────────────────────

class TestRiskRouter:

    def test_post_required_not_get(self, client):
        resp = client.get("/api/v1/predict/risk")
        assert resp.status_code == 405  # Method Not Allowed

    def test_valid_run_returns_200_with_risk_array(self, client, seeded_complexity_context):
        resp = client.post("/api/v1/predict/risk", json={"run_id": seeded_complexity_context.run_id})
        assert resp.status_code == 200
        body = resp.json()
        assert "risk" in body
        assert "high_risk_count" in body

    def test_risk_score_always_in_range(self, client, seeded_complexity_context):
        resp = client.post("/api/v1/predict/risk", json={"run_id": seeded_complexity_context.run_id})
        for item in resp.json()["risk"]:
            assert 0.0 <= item["risk_score"] <= 1.0

    def test_no_complexity_returns_424(self, client, empty_run_context):
        resp = client.post("/api/v1/predict/risk", json={"run_id": empty_run_context.run_id})
        assert resp.status_code == 424


# ── Context Propagation Tests ────────────────────────────────────────────────

class TestRiskContextPropagation:

    @pytest.mark.asyncio
    async def test_risk_checkpointed(self, run_context, context_store):
        from app.modules.risk.service import run_risk
        await run_risk(run_context, context_store)
        stored = await context_store.get(run_context.run_id)
        assert stored.stage_flags["risk"].value == "COMPLETE"
        assert len(stored.risk) > 0

    @pytest.mark.asyncio
    async def test_risk_available_for_healer(self, run_context):
        assert len(run_context.risk) > 0, "risk must be set by previous test in session"
```

---

## Running Tests

```bash
pytest tests/test_03_risk.py -v
pytest tests/test_01_analyzer.py tests/test_02_complexity.py tests/test_03_risk.py -v
pytest tests/test_03_risk.py --cov=app/modules/risk --cov-report=term-missing
```

---

## Common Issues & Resolutions

**Issue:** All risk scores are `0.0` for every file.
**Resolution:** If all files have identical feature values, min-max normalisation produces all
zeros (denominator = 0). This is expected and handled. Add a log warning when `max_s == min_s`.

**Issue:** `contamination='auto'` produces unexpected results on small repos (<10 files).
**Resolution:** For repos with fewer than 10 files, use `contamination=0.1` explicitly.
Isolation Forest's automatic contamination estimation is unstable on very small datasets.

**Issue:** `score_samples` returns values outside `[-1, 0]`.
**Resolution:** Theoretically bounded to `[-0.5, 0]` for normal data, but numerical precision
can produce small outliers. The inversion + min-max normalisation handles this correctly.

**Issue:** Risk prediction is non-deterministic across runs.
**Resolution:** Always pass `random_state=42` to `IsolationForest`. Without it, different runs
on the same data will produce different tree partitions and different score rankings.
