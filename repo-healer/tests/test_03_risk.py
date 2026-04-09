"""Tests for Module 03 — Risk Prediction Agent."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.schemas import RiskLevel
from app.modules.risk.service import (
    _score_to_level,
    build_feature_matrix,
    run_isolation_forest,
)


class TestBuildFeatureMatrix:

    def test_returns_correct_columns(self, run_context_with_complexity):
        df, files = build_feature_matrix(run_context_with_complexity)
        assert list(df.columns) == [
            "total_churn",
            "commit_count",
            "contributors",
            "complexity_adj",
            "mi_inverted",
        ]

    def test_parse_error_files_excluded_from_matrix(self, run_context_with_parse_error):
        df, files = build_feature_matrix(run_context_with_parse_error)
        assert not any("broken" in f for f in files)

    def test_file_count_matches_non_error_records(self, run_context_with_complexity):
        df, files = build_feature_matrix(run_context_with_complexity)
        non_error = sum(
            1 for r in run_context_with_complexity.complexity if not r.parse_error
        )
        assert len(df) == non_error


class TestRunIsolationForest:

    def test_scores_in_zero_to_one_range(self, sample_feature_df):
        scores, _, _ = run_isolation_forest(sample_feature_df)
        assert np.all(scores >= 0.0)
        assert np.all(scores <= 1.0)

    def test_high_churn_file_scores_higher(self):
        normal = pd.DataFrame(
            [
                {
                    "total_churn": 10,
                    "commit_count": 2,
                    "contributors": 1,
                    "complexity_adj": 3.0,
                    "mi_inverted": 30.0,
                }
                for _ in range(10)
            ]
        )
        anomaly = pd.DataFrame(
            [
                {
                    "total_churn": 5000,
                    "commit_count": 200,
                    "contributors": 30,
                    "complexity_adj": 50.0,
                    "mi_inverted": 90.0,
                }
            ]
        )
        df = pd.concat([normal, anomaly], ignore_index=True)
        scores, _, _ = run_isolation_forest(df)
        assert scores[-1] > scores[:-1].mean()

    def test_single_file_returns_zero_score(self):
        df = pd.DataFrame(
            [
                {
                    "total_churn": 5,
                    "commit_count": 1,
                    "contributors": 1,
                    "complexity_adj": 2.0,
                    "mi_inverted": 20.0,
                }
            ]
        )
        scores, _, _ = run_isolation_forest(df)
        assert len(scores) == 1
        assert scores[0] == 0.0

    def test_model_deterministic_with_random_state(self, sample_feature_df):
        s1, _, _ = run_isolation_forest(sample_feature_df)
        s2, _, _ = run_isolation_forest(sample_feature_df)
        np.testing.assert_array_almost_equal(s1, s2)


class TestScoreToLevel:

    @pytest.mark.parametrize(
        "score,expected",
        [
            (0.75, RiskLevel.HIGH),
            (0.70, RiskLevel.HIGH),
            (0.50, RiskLevel.MEDIUM),
            (0.40, RiskLevel.MEDIUM),
            (0.39, RiskLevel.LOW),
            (0.0, RiskLevel.LOW),
        ],
    )
    def test_thresholds(self, score, expected):
        assert _score_to_level(score) == expected

    def test_above_one_treated_as_high(self):
        assert _score_to_level(1.01) == RiskLevel.HIGH
