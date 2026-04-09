# Module 01 — Repo Analyzer Agent

## Purpose

The Repo Analyzer Agent is the **first stage** of the Repo Healer pipeline. It mines a Git
repository's commit history using PyDriller and produces a file-level metadata table that
every downstream agent depends on. The output captures four dimensions of historical activity
per file: total churn (lines added + deleted), commit frequency, contributor count, and
recency of last modification.

These raw signals are the feature inputs for the Risk Prediction Agent's Isolation Forest model.
Without accurate churn and commit data, risk scores are meaningless.

---

## Tech Stack

| Dependency    | Version  | Role                                                    |
|---------------|----------|---------------------------------------------------------|
| FastAPI       | ≥0.111   | HTTP router, request validation, OpenAPI docs           |
| Pydantic v2   | ≥2.7     | `AnalysisRequest`, `FileRecord` schema validation       |
| PyDriller     | ≥2.5     | Git commit traversal, diff parsing, churn extraction    |
| structlog     | ≥24.1    | Structured JSON logging with `run_id` context           |
| orjson        | ≥3.10    | Fast serialisation of `FileRecord` list to context store|
| pytest        | ≥8.0     | Unit + integration tests                                |
| pytest-mock   | ≥3.12    | Mock PyDriller `Repository` for unit tests              |
| httpx         | ≥0.27    | Async HTTP client for router integration tests          |

---

## API Endpoint

### `GET /api/v1/analyze/repo`

Clones (or uses a cached local copy of) the target repository, traverses commits, and returns
file-level metrics. This is a `GET` endpoint — all parameters are passed as query strings, not
a JSON body. Sending a JSON body on a `GET` request is non-standard and rejected by most
reverse proxies.

**Query Parameters:**

| Parameter         | Type   | Required | Default  | Description                                       |
|-------------------|--------|----------|----------|---------------------------------------------------|
| `repo_url`        | string | ✅       | —        | HTTPS or SSH URL of the target repository          |
| `branch`          | string | ❌       | `main`   | Branch to traverse                                |
| `last_commit_sha` | string | ❌       | `None`   | If provided, traversal stops at this SHA (inclusive)|
| `run_id`          | string | ❌       | auto     | Attach result to an existing run context           |
| `since_days`      | int    | ❌       | `None`   | Limit analysis to commits in the last N days       |

**Success Response — `200 OK`:**

```json
{
  "run_id": "20241120-143200",
  "file_count": 47,
  "analysis": [
    {
      "file": "src/utils.py",
      "total_churn": 142,
      "commit_count": 17,
      "contributors": 3,
      "last_modified": "2024-11-20T14:32:00Z",
      "extensions": ".py",
      "is_deleted": false
    }
  ]
}
```

**Error Responses:**

| Status | Condition                                           |
|--------|-----------------------------------------------------|
| 422    | Missing `repo_url` or invalid query param types     |
| 502    | PyDriller fails to clone the repository             |
| 504    | Clone or traversal exceeds `ANALYSIS_TIMEOUT_SECS`  |

---

## Service: `app/modules/analyzer/service.py`

### Key Design Decisions

**1. Incremental traversal via `last_commit_sha`**

PyDriller's `Repository` accepts `to_commit` to stop iteration at a specific SHA. For large
repositories this is critical — without it, every run re-traverses the full history. The service
uses `to_commit=last_commit_sha` when the parameter is provided.

```python
from pydriller import Repository

def traverse_repo(repo_url: str, branch: str, to_commit: str | None) -> list[FileRecord]:
    kwargs = {
        "path_to_repo": repo_url,
        "only_in_branch": branch,
        "only_modifications_with_file_types": settings.file_extensions,
        "only_no_merge": True,   # exclude merge commits — they inflate churn unfairly
    }
    if to_commit:
        kwargs["to_commit"] = to_commit

    file_stats: dict[str, _FileStat] = {}

    for commit in Repository(**kwargs).traverse_commits():
        for mod in commit.modified_files:
            if mod.new_path is None:  # file was deleted
                continue
            stat = file_stats.setdefault(mod.new_path, _FileStat(file=mod.new_path))
            stat.total_churn   += (mod.added_lines or 0) + (mod.deleted_lines or 0)
            stat.commit_count  += 1
            stat.contributors.add(commit.author.email)
            stat.last_modified  = commit.author_date

    return [stat.to_record() for stat in file_stats.values()]
```

**2. Merge commit exclusion (`only_no_merge=True`)**

Merge commits contain the cumulative diff of the merged branch. Including them would
double-count churn for every file touched in a feature branch and make churn scores
unreliable as a risk signal. `only_no_merge=True` is mandatory, not optional.

**3. Deleted file exclusion**

Files where `mod.new_path is None` have been deleted from the repository. Including them
in the output would cause downstream agents to attempt complexity analysis on non-existent
files. They are filtered out explicitly.

**4. Author identity by email, not username**

PyDriller's `commit.author.name` is a free-text field set locally by each contributor and
is prone to duplicates (e.g. "John", "john doe", "jdoe"). `commit.author.email` is a more
stable unique identifier and is used for contributor de-duplication in a `set`.

**5. `only_no_merge` vs `include_remotes`**

PyDriller by default only traverses local refs. For repositories cloned from a remote, this
is fine — PyDriller handles the clone transparently. Do not set `include_remotes=True` as
this can expose refs from stale remote tracking branches that no longer exist on origin.

---

## Schemas: `app/modules/analyzer/schemas.py`

```python
from pydantic import BaseModel, field_validator
from datetime import datetime

class FileRecord(BaseModel):
    file:          str
    total_churn:   int       = 0
    commit_count:  int       = 0
    contributors:  int       = 0
    last_modified: datetime
    extension:     str       = ""
    is_deleted:    bool      = False

    @field_validator("total_churn", "commit_count", "contributors")
    @classmethod
    def non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("metric must be non-negative")
        return v

class AnalysisResult(BaseModel):
    run_id:     str
    file_count: int
    analysis:   list[FileRecord]
```

---

## Context Store Integration

After a successful analysis, the service writes results into `RunContext.analysis` and
checkpoints:

```python
async def run_analysis(ctx: RunContext, store: ContextStore) -> AnalysisResult:
    records = traverse_repo(ctx.repo_url, ctx.branch, ctx.last_commit_sha)
    ctx.analysis = records
    ctx.stage_flags["analysis"] = StageStatus.COMPLETE
    ctx.last_updated = datetime.utcnow()
    await store.set(ctx.run_id, ctx)   # ← checkpoint
    return AnalysisResult(run_id=ctx.run_id, file_count=len(records), analysis=records)
```

The checkpoint after `analysis` means that if the pipeline is interrupted before the
complexity stage, `ctx.analysis` is preserved and need not be recomputed.

---

## Known PyDriller Pitfalls & Mitigations

| Issue | Root Cause | Mitigation |
|---|---|---|
| 1 commit/800ms on large repos | PyDriller processes diffs serially via GitPython | Use `since_days` or `last_commit_sha` to limit scope |
| `AttributeError` on some commits | Malformed git objects in some repos | Wrap `commit.modified_files` in `try/except` and log + skip |
| Remote clone timeout | Large repos on slow connections | `ANALYSIS_TIMEOUT_SECS` env var (default 300s); SIGALRM interrupt |
| Non-UTF-8 file paths | Repos with non-ASCII filenames | `mod.new_path.encode("utf-8", errors="replace")` |
| Binary file churn | Images, compiled artifacts inflate churn | `only_modifications_with_file_types=[".py"]` default; configurable |
| Deleted files appearing in output | `mod.new_path is None` for deletions | Explicit `if mod.new_path is None: continue` guard |

---

## Testing Module

Tests live in `tests/test_01_analyzer.py`. They are designed to propagate context forward to
downstream test modules via pytest's `session`-scoped fixture in `tests/conftest.py`.

### Context Propagation Strategy

The key challenge in a multi-agent pipeline test suite is that each test module needs the
output of the previous one. We solve this with a **session-scoped context store fixture**
in `conftest.py` that is shared across all test modules:

```python
# tests/conftest.py
import pytest
from app.core.context_store import InMemoryContextStore
from app.core.schemas import RunContext
from datetime import datetime
import uuid

@pytest.fixture(scope="session")
def context_store():
    """Shared in-memory context store for the entire test session."""
    return InMemoryContextStore()

@pytest.fixture(scope="session")
def run_context(context_store):
    """
    Creates a RunContext at session start.
    All test modules update this same context object, propagating
    stage outputs forward without losing state between test files.
    """
    ctx = RunContext(
        run_id=f"test-{uuid.uuid4().hex[:8]}",
        repo_url="file:///tmp/test-repo",   # local git fixture created by seed script
        branch="main",
        started_at=datetime.utcnow(),
        last_updated=datetime.utcnow(),
    )
    return ctx
```

Because both `context_store` and `run_context` are `scope="session"`, they are instantiated
once and shared across all 7 test files. Each test file mutates `run_context` by adding its
stage output, and the next test file reads it directly — exactly mirroring the production
pipeline's behaviour.

### Test Cases: `tests/test_01_analyzer.py`

```python
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app.modules.analyzer.service import traverse_repo
from app.modules.analyzer.schemas import FileRecord

client = TestClient(app)

# ── Unit Tests ──────────────────────────────────────────────────────────────

class TestTraverseRepo:

    def test_returns_list_of_file_records(self, mock_repository):
        """traverse_repo returns a non-empty list of FileRecord objects."""
        records = traverse_repo("https://github.com/test/repo", "main", None)
        assert isinstance(records, list)
        assert len(records) > 0
        assert all(isinstance(r, FileRecord) for r in records)

    def test_excludes_deleted_files(self, mock_repository_with_deletion):
        """Files with new_path=None (deletions) are excluded from output."""
        records = traverse_repo("https://github.com/test/repo", "main", None)
        assert all(not r.is_deleted for r in records)
        assert all(r.file is not None for r in records)

    def test_respects_last_commit_sha(self, mock_repository):
        """to_commit parameter is passed to PyDriller Repository."""
        with patch("app.modules.analyzer.service.Repository") as MockRepo:
            MockRepo.return_value.__iter__ = lambda self: iter([])
            traverse_repo("url", "main", "abc123")
            call_kwargs = MockRepo.call_args.kwargs
            assert call_kwargs.get("to_commit") == "abc123"

    def test_excludes_merge_commits(self, mock_repository):
        """only_no_merge=True is always passed to Repository."""
        with patch("app.modules.analyzer.service.Repository") as MockRepo:
            MockRepo.return_value.__iter__ = lambda self: iter([])
            traverse_repo("url", "main", None)
            assert MockRepo.call_args.kwargs.get("only_no_merge") is True

    def test_deduplicates_contributors_by_email(self, mock_repository_same_author):
        """Two commits by same email produce contributors=1."""
        records = traverse_repo("url", "main", None)
        target = next(r for r in records if r.file == "src/main.py")
        assert target.contributors == 1

    def test_churn_sums_added_and_deleted_lines(self, mock_repository_known_churn):
        """total_churn = added_lines + deleted_lines across all commits."""
        records = traverse_repo("url", "main", None)
        target = next(r for r in records if r.file == "src/utils.py")
        assert target.total_churn == 50  # 30 added + 20 deleted in fixture

    def test_non_utf8_path_handled(self, mock_repository_non_utf8):
        """Files with non-UTF-8 paths do not raise an exception."""
        records = traverse_repo("url", "main", None)
        assert isinstance(records, list)  # should not raise

    def test_empty_repo_returns_empty_list(self, mock_empty_repository):
        """A repo with no qualifying commits returns []."""
        records = traverse_repo("url", "main", None)
        assert records == []

    def test_pydriller_exception_raises_analysis_error(self):
        """PyDriller errors are wrapped in AnalysisError, not propagated raw."""
        from app.core.exceptions import AnalysisError
        with patch("app.modules.analyzer.service.Repository", side_effect=Exception("git error")):
            with pytest.raises(AnalysisError, match="git error"):
                traverse_repo("url", "main", None)

    def test_only_python_files_by_default(self, mock_repository_multi_ext):
        """Only .py files appear when FILE_EXTENSIONS=['.py'] (default)."""
        records = traverse_repo("url", "main", None)
        assert all(r.file.endswith(".py") for r in records)


# ── Router Integration Tests ────────────────────────────────────────────────

class TestAnalyzerRouter:

    def test_missing_repo_url_returns_422(self):
        """GET /analyze/repo without repo_url returns HTTP 422."""
        resp = client.get("/api/v1/analyze/repo")
        assert resp.status_code == 422

    def test_valid_request_returns_200(self, mock_traverse_repo_service):
        """Valid query params return HTTP 200 with analysis payload."""
        resp = client.get(
            "/api/v1/analyze/repo",
            params={"repo_url": "https://github.com/test/repo", "branch": "main"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "analysis" in body
        assert "run_id" in body
        assert isinstance(body["analysis"], list)

    def test_response_schema_matches_file_record(self, mock_traverse_repo_service):
        """Each item in analysis array matches FileRecord schema."""
        resp = client.get(
            "/api/v1/analyze/repo",
            params={"repo_url": "https://github.com/test/repo"},
        )
        for item in resp.json()["analysis"]:
            record = FileRecord(**item)  # Pydantic validation
            assert record.total_churn >= 0
            assert record.commit_count >= 0

    def test_json_body_on_get_is_ignored(self, mock_traverse_repo_service):
        """Sending a JSON body on GET does not cause a 400 or 500 error."""
        resp = client.get(
            "/api/v1/analyze/repo",
            params={"repo_url": "https://github.com/test/repo"},
            content=b'{"repo_url": "ignored"}',
        )
        assert resp.status_code == 200

    def test_pydriller_502_on_clone_failure(self):
        """PyDriller clone failure returns HTTP 502, not 500."""
        from app.core.exceptions import AnalysisError
        with patch(
            "app.modules.analyzer.service.traverse_repo",
            side_effect=AnalysisError("clone failed"),
        ):
            resp = client.get(
                "/api/v1/analyze/repo",
                params={"repo_url": "https://invalid.example.com/repo"},
            )
            assert resp.status_code == 502


# ── Context Store Tests ─────────────────────────────────────────────────────

class TestAnalyzerContextPropagation:

    @pytest.mark.asyncio
    async def test_checkpoint_written_after_analysis(
        self, run_context, context_store, mock_traverse_repo_service
    ):
        """After run_analysis(), RunContext.analysis is populated and checkpointed."""
        from app.modules.analyzer.service import run_analysis
        result = await run_analysis(run_context, context_store)
        stored = await context_store.get(run_context.run_id)
        assert stored is not None
        assert len(stored.analysis) > 0
        assert stored.stage_flags["analysis"].value == "COMPLETE"

    @pytest.mark.asyncio
    async def test_analysis_persisted_for_downstream(
        self, run_context, context_store
    ):
        """
        RunContext.analysis set here is accessible by test_02_complexity.py
        because run_context is session-scoped.
        """
        assert len(run_context.analysis) > 0, (
            "analysis should have been populated by TestAnalyzerContextPropagation "
            "test above — check test execution order or session fixture"
        )


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
```

### Fixtures (`tests/conftest.py` additions for this module)

```python
@pytest.fixture
def mock_repository(mocker):
    """Minimal PyDriller Repository mock returning two commits on one file."""
    commit = MagicMock()
    commit.author.email = "alice@example.com"
    commit.author_date = datetime(2024, 11, 20, 14, 32)
    mod = MagicMock()
    mod.new_path = "src/utils.py"
    mod.added_lines = 30
    mod.deleted_lines = 20
    commit.modified_files = [mod]
    mocker.patch(
        "app.modules.analyzer.service.Repository",
        return_value=iter([commit]),
    )

@pytest.fixture
def mock_traverse_repo_service(mocker):
    """Patches the service function so router tests don't need git access."""
    from app.modules.analyzer.schemas import FileRecord
    from datetime import datetime
    mocker.patch(
        "app.modules.analyzer.service.traverse_repo",
        return_value=[
            FileRecord(
                file="src/utils.py",
                total_churn=142,
                commit_count=17,
                contributors=3,
                last_modified=datetime(2024, 11, 20, 14, 32),
            )
        ],
    )
```

---

## Running Tests for This Module Only

```bash
# Unit + integration tests for analyzer only
pytest tests/test_01_analyzer.py -v

# With coverage report
pytest tests/test_01_analyzer.py --cov=app/modules/analyzer --cov-report=term-missing

# Including context propagation check (requires session fixtures)
pytest tests/test_01_analyzer.py tests/test_02_complexity.py -v --tb=short
```

---

## Common Issues & Resolutions

**Issue:** `GitCommandNotFound` when running tests locally.
**Resolution:** PyDriller requires `git` on `PATH`. Install with `apt install git` or `brew install git`.

**Issue:** Traversal hangs on a very large private repo.
**Resolution:** Set `ANALYSIS_TIMEOUT_SECS=60` and provide `last_commit_sha` to limit scope.

**Issue:** `only_in_branch` causes `KeyError` for repos with no `main` branch.
**Resolution:** Check `repo.head.reference.name` before passing `only_in_branch`. The service
defaults to `main` but falls back to the repo's default branch if `main` does not exist.

**Issue:** Churn numbers are unexpectedly high on certain files.
**Resolution:** Check for missing `only_no_merge=True`. Without this flag, merge commits
accumulate all diffs from the merged branch, inflating churn by 2–5× on active projects.

**Issue:** `AttributeError: 'NoneType' object has no attribute 'added_lines'`
**Resolution:** Some malformed commits return `None` for `modified_files` elements. Guard with
`if mod is None or mod.new_path is None: continue`.
