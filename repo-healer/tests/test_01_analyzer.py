"""Tests for Module 01 — Repo Analyzer Agent."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.core.schemas import FileRecord


# ── Schema Validation Tests ──────────────────────────────────────────────────


class TestFileRecordSchema:

    def test_negative_churn_raises_validation_error(self):
        with pytest.raises(Exception):
            FileRecord(
                file="src/x.py",
                total_churn=-1,
                commit_count=5,
                contributors=1,
                last_modified="2024-01-01T00:00:00Z",
            )

    def test_zero_values_are_valid(self):
        r = FileRecord(
            file="src/empty.py",
            total_churn=0,
            commit_count=0,
            contributors=0,
            last_modified="2024-01-01T00:00:00Z",
        )
        assert r.total_churn == 0

    def test_last_modified_parses_iso_string(self):
        r = FileRecord(
            file="src/x.py",
            total_churn=10,
            commit_count=2,
            contributors=1,
            last_modified="2024-11-20T14:32:00Z",
        )
        assert r.last_modified.year == 2024


# ── Service Unit Tests ───────────────────────────────────────────────────────


class TestTraverseRepo:

    def test_excludes_merge_commits(self):
        """only_no_merge=True is always passed to Repository."""
        with patch("app.modules.analyzer.service.Repository") as MockRepo:
            mock_iter = MagicMock()
            mock_iter.traverse_commits.return_value = iter([])
            MockRepo.return_value = mock_iter

            from app.modules.analyzer.service import traverse_repo

            traverse_repo("url", "main", None)
            assert MockRepo.call_args.kwargs.get("only_no_merge") is True

    def test_respects_last_commit_sha(self):
        """to_commit parameter is passed to PyDriller Repository."""
        with patch("app.modules.analyzer.service.Repository") as MockRepo:
            mock_iter = MagicMock()
            mock_iter.traverse_commits.return_value = iter([])
            MockRepo.return_value = mock_iter

            from app.modules.analyzer.service import traverse_repo

            traverse_repo("url", "main", "abc123")
            call_kwargs = MockRepo.call_args.kwargs
            assert call_kwargs.get("to_commit") == "abc123"

    def test_empty_repo_returns_empty_list(self):
        """A repo with no qualifying commits returns []."""
        with patch("app.modules.analyzer.service.Repository") as MockRepo:
            mock_iter = MagicMock()
            mock_iter.traverse_commits.return_value = iter([])
            MockRepo.return_value = mock_iter

            from app.modules.analyzer.service import traverse_repo

            records = traverse_repo("url", "main", None)
            assert records == []

    def test_pydriller_exception_raises_analysis_error(self):
        """PyDriller errors are wrapped in AnalysisError."""
        from app.core.exceptions import AnalysisError

        with patch(
            "app.modules.analyzer.service.Repository",
            side_effect=Exception("git error"),
        ):
            from app.modules.analyzer.service import traverse_repo

            with pytest.raises(AnalysisError, match="git error"):
                traverse_repo("url", "main", None)


# ── Router Integration Tests ────────────────────────────────────────────────


class TestAnalyzerRouter:

    def test_missing_repo_url_returns_422(self, client):
        """GET /analyze/repo without repo_url returns HTTP 422."""
        resp = client.get("/api/v1/analyze/repo")
        assert resp.status_code == 422
