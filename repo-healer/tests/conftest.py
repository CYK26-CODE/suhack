"""Session-scoped fixtures and context propagation for the test suite."""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.core.context_store import InMemoryContextStore
from app.core.schemas import (
    ComplexityRecord,
    FileRecord,
    HealResult,
    RiskLevel,
    RiskRecord,
    RunContext,
    StageStatus,
    ValidationResult,
)


# ── Session-scoped fixtures (shared across all test files) ───────────────────


@pytest.fixture(scope="session")
def context_store():
    """Shared in-memory context store for the entire test session."""
    return InMemoryContextStore()


@pytest.fixture(scope="session")
def run_context(context_store):
    """Session-scoped RunContext that accumulates stage outputs across tests."""
    ctx = RunContext(
        run_id=f"test-{uuid.uuid4().hex[:8]}",
        repo_url="file:///tmp/test-repo",
        branch="main",
        local_repo_path="/tmp/test-repo",
        started_at=datetime.utcnow(),
        last_updated=datetime.utcnow(),
    )
    return ctx


# ── Module-scoped convenience fixtures ───────────────────────────────────────


@pytest.fixture
def client():
    """FastAPI TestClient."""
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


@pytest.fixture
def empty_run_context():
    """A fresh RunContext with no stage data."""
    return RunContext(
        run_id=f"empty-{uuid.uuid4().hex[:8]}",
        repo_url="https://github.com/test/empty",
        branch="main",
    )


@pytest.fixture
def sample_file_records():
    """A list of FileRecord fixtures."""
    return [
        FileRecord(
            file="src/utils.py",
            total_churn=142,
            commit_count=17,
            contributors=3,
            last_modified=datetime(2024, 11, 20, 14, 32),
        ),
        FileRecord(
            file="src/main.py",
            total_churn=30,
            commit_count=5,
            contributors=1,
            last_modified=datetime(2024, 11, 19, 10, 0),
        ),
    ]


@pytest.fixture
def sample_complexity_records():
    """A list of ComplexityRecord fixtures."""
    return [
        ComplexityRecord(
            file="src/utils.py",
            complexity=8.4,
            maintainability=52.3,
            function_count=12,
        ),
        ComplexityRecord(
            file="src/main.py",
            complexity=2.0,
            maintainability=85.0,
            function_count=3,
        ),
    ]


@pytest.fixture
def run_context_with_complexity(sample_file_records, sample_complexity_records):
    """RunContext populated with analysis + complexity data."""
    ctx = RunContext(
        run_id=f"cx-{uuid.uuid4().hex[:8]}",
        repo_url="https://github.com/test/repo",
        branch="main",
    )
    ctx.analysis = sample_file_records
    ctx.complexity = sample_complexity_records
    return ctx


@pytest.fixture
def run_context_with_parse_error(sample_file_records):
    """RunContext with one file having a parse error."""
    ctx = RunContext(
        run_id=f"pe-{uuid.uuid4().hex[:8]}",
        repo_url="https://github.com/test/repo",
        branch="main",
    )
    ctx.analysis = sample_file_records + [
        FileRecord(
            file="src/broken.py",
            total_churn=20,
            commit_count=3,
            contributors=1,
            last_modified=datetime(2024, 1, 1),
        )
    ]
    ctx.complexity = [
        ComplexityRecord(
            file="src/utils.py", complexity=8.4, maintainability=52.3, function_count=12
        ),
        ComplexityRecord(
            file="src/main.py", complexity=2.0, maintainability=85.0, function_count=3
        ),
        ComplexityRecord(
            file="src/broken.py",
            complexity=-1.0,
            maintainability=-1.0,
            function_count=0,
            parse_error=True,
        ),
    ]
    return ctx


@pytest.fixture
def sample_feature_df():
    """A sample feature DataFrame for risk service tests."""
    import pandas as pd

    normal_rows = [
        {"total_churn": 10, "commit_count": 2, "contributors": 1,
         "complexity_adj": 3.0, "mi_inverted": 30.0}
        for _ in range(10)
    ]
    anomaly_row = {
        "total_churn": 5000, "commit_count": 200, "contributors": 30,
        "complexity_adj": 50.0, "mi_inverted": 90.0,
    }
    return pd.DataFrame(normal_rows + [anomaly_row])
