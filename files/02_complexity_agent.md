# Module 02 — Complexity Agent

## Purpose

The Complexity Agent is the **second stage** of the pipeline. It reads the list of `FileRecord`
objects produced by the Analyzer and enriches each entry with two static-analysis metrics:

- **Average cyclomatic complexity** — the mean decision-point count across all functions/methods
  in the file, computed via Radon's `cc_visit`.
- **Maintainability Index (MI)** — Radon's composite score (0–100) derived from Halstead
  volume, cyclomatic complexity, and lines of code. Lower is harder to maintain.

These two numbers are the primary complexity features fed into the Isolation Forest risk model.

---

## Tech Stack

| Dependency    | Version | Role                                                          |
|---------------|---------|---------------------------------------------------------------|
| FastAPI       | ≥0.111  | HTTP router                                                   |
| Pydantic v2   | ≥2.7    | `ComplexityRecord`, `ComplexityResult` schema validation       |
| radon         | ≥6.0    | `cc_visit` (cyclomatic), `mi_visit` (maintainability index)   |
| structlog     | ≥24.1   | Structured logging                                            |
| pytest        | ≥8.0    | Test runner                                                   |
| pytest-mock   | ≥3.12   | Mocking radon calls                                           |
| httpx         | ≥0.27   | Router integration tests                                      |

---

## API Endpoint

### `POST /api/v1/analyze/complexity`

Reads `RunContext.analysis` from the context store and runs Radon over each file. The source
code of each file is retrieved from the cloned repository path stored in the context.

**Request Body:**

```json
{
  "run_id": "20241120-143200"
}
```

If `run_id` is omitted, the most recent context in the store is used (development convenience
only — do not rely on this in production or tests).

**Success Response — `200 OK`:**

```json
{
  "run_id": "20241120-143200",
  "complexity": [
    {
      "file": "src/utils.py",
      "complexity": 8.4,
      "maintainability": 52.3,
      "function_count": 12,
      "parse_error": false
    }
  ]
}
```

**Error Responses:**

| Status | Condition                              |
|--------|----------------------------------------|
| 404    | `run_id` not found in context store    |
| 422    | Malformed request body                 |
| 424    | `RunContext.analysis` is empty (the analyzer stage has not run yet) |

---

## Service: `app/modules/complexity/service.py`

### Aggregation Strategy: Average Cyclomatic Complexity Per File

Radon's `cc_visit(source_code)` returns a list of `Function`/`Method` objects, each with a
`.complexity` integer. The correct aggregation for a **file-level** complexity score is the
**arithmetic mean** across all functions in the file.

Using the raw maximum would over-weight a single complex function and penalise files that are
large but otherwise well-structured. Using the sum would make large files always appear riskier
than small ones regardless of actual function complexity.

```python
from radon.complexity import cc_visit, cc_rank
from radon.metrics import mi_visit

def compute_complexity(file_path: str, source_code: str) -> ComplexityRecord:
    try:
        functions = cc_visit(source_code)
        if functions:
            avg_complexity = sum(f.complexity for f in functions) / len(functions)
        else:
            avg_complexity = 0.0

        mi_score = mi_visit(source_code, multi=True)
        return ComplexityRecord(
            file=file_path,
            complexity=round(avg_complexity, 2),
            maintainability=round(mi_score, 2),
            function_count=len(functions),
            parse_error=False,
        )
    except SyntaxError as exc:
        # Syntax errors must not stall the pipeline
        log.warning("syntax_error_in_file", file=file_path, error=str(exc))
        return ComplexityRecord(
            file=file_path,
            complexity=-1.0,
            maintainability=-1.0,
            function_count=0,
            parse_error=True,
        )
    except Exception as exc:
        log.error("complexity_unexpected_error", file=file_path, error=str(exc))
        return ComplexityRecord(
            file=file_path,
            complexity=-1.0,
            maintainability=-1.0,
            function_count=0,
            parse_error=True,
        )
```

**Why sentinel `-1.0` instead of `None`?**

The Risk Agent expects a numeric feature matrix. `None` values would require imputation logic.
`-1.0` is outside the valid range of both metrics (complexity ≥ 0, MI ∈ [0, 100]) and serves
as an unambiguous signal that the file could not be parsed. The Risk Agent treats `-1.0` files
as always-HIGH risk — a conservative fallback.

### Reading Source Code from the Cloned Repo

The analyzer stage clones the repository to a temporary directory stored in
`RunContext.local_repo_path`. The complexity service reads files from that path:

```python
import aiofiles

async def read_source(local_repo_path: str, file: str) -> str:
    full_path = pathlib.Path(local_repo_path) / file
    async with aiofiles.open(full_path, encoding="utf-8", errors="replace") as f:
        return await f.read()
```

`errors="replace"` prevents encoding errors on files with non-UTF-8 content from halting the
entire batch.

---

## Schemas

```python
class ComplexityRecord(BaseModel):
    file:           str
    complexity:     float   # average cyclomatic complexity; -1.0 if parse error
    maintainability: float  # Radon MI (0-100); -1.0 if parse error
    function_count: int     = 0
    parse_error:    bool    = False

    @field_validator("complexity", "maintainability")
    @classmethod
    def sentinel_or_valid(cls, v: float, info) -> float:
        if v == -1.0:
            return v  # sentinel allowed
        if info.field_name == "complexity" and v < 0:
            raise ValueError("complexity must be >= 0")
        if info.field_name == "maintainability" and not (-1.0 <= v <= 100.0):
            raise ValueError("maintainability must be in [0, 100] or -1.0 sentinel")
        return v
```

---

## Radon Metric Reference

**Cyclomatic Complexity (CC):**

| Score | Radon Grade | Risk                             |
|-------|-------------|----------------------------------|
| 1–5   | A           | Low — simple, well-structured    |
| 6–10  | B           | Medium — moderate risk           |
| 11–15 | C           | High — more complex              |
| 16–20 | D           | Very high                        |
| 21–25 | E           | Extremely complex                |
| >25   | F           | Untestable                       |

The pipeline's Risk Agent uses the raw average float, not the letter grade, to preserve
granularity for the Isolation Forest feature matrix.

**Maintainability Index (MI):**

| Score  | Interpretation                   |
|--------|----------------------------------|
| 100–20 | Highly maintainable              |
| 20–10  | Moderate                         |
| <10    | Difficult to maintain            |

Radon's `mi_visit` returns a float in [0, 100]. The `multi=True` parameter enables the
multi-line string handling that avoids false-positive complexity on docstrings.

---

## Context Store Integration

```python
async def run_complexity(ctx: RunContext, store: ContextStore) -> ComplexityResult:
    if not ctx.analysis:
        raise ComplexityError("analysis stage must run before complexity")

    records = []
    for file_record in ctx.analysis:
        source = await read_source(ctx.local_repo_path, file_record.file)
        records.append(compute_complexity(file_record.file, source))

    ctx.complexity = records
    ctx.stage_flags["complexity"] = StageStatus.COMPLETE
    ctx.last_updated = datetime.utcnow()
    await store.set(ctx.run_id, ctx)   # checkpoint
    return ComplexityResult(run_id=ctx.run_id, complexity=records)
```

---

## Testing Module: `tests/test_02_complexity.py`

This module's tests depend on `run_context.analysis` being populated by `test_01_analyzer.py`.
Because `run_context` is `scope="session"`, the data is available as long as the analyzer
tests ran first in the same session.

```python
import pytest
from app.modules.complexity.service import compute_complexity
from app.modules.complexity.schemas import ComplexityRecord

# ── Unit Tests ──────────────────────────────────────────────────────────────

SIMPLE_SOURCE = """\
def add(a, b):
    return a + b
"""

COMPLEX_SOURCE = """\
def process(x, y, z):
    if x > 0:
        if y > 0:
            for i in range(z):
                if i % 2 == 0:
                    pass
                else:
                    pass
        elif y < 0:
            pass
    else:
        while x < 10:
            x += 1
    return x
"""

SYNTAX_ERROR_SOURCE = "def broken(:\n    pass"

class TestComputeComplexity:

    def test_simple_function_has_low_complexity(self):
        record = compute_complexity("src/add.py", SIMPLE_SOURCE)
        assert record.complexity >= 1.0
        assert record.complexity <= 3.0
        assert not record.parse_error

    def test_complex_function_has_higher_complexity(self):
        simple = compute_complexity("src/add.py", SIMPLE_SOURCE)
        complex_ = compute_complexity("src/process.py", COMPLEX_SOURCE)
        assert complex_.complexity > simple.complexity

    def test_syntax_error_returns_sentinel(self):
        record = compute_complexity("src/broken.py", SYNTAX_ERROR_SOURCE)
        assert record.parse_error is True
        assert record.complexity == -1.0
        assert record.maintainability == -1.0

    def test_empty_file_returns_zero_complexity(self):
        record = compute_complexity("src/empty.py", "")
        assert record.complexity == 0.0
        assert not record.parse_error

    def test_function_count_matches_radon(self):
        record = compute_complexity("src/two_funcs.py", SIMPLE_SOURCE + "\ndef subtract(a, b):\n    return a - b\n")
        assert record.function_count == 2

    def test_maintainability_in_valid_range(self):
        record = compute_complexity("src/add.py", SIMPLE_SOURCE)
        assert 0.0 <= record.maintainability <= 100.0

    def test_parse_error_does_not_raise(self):
        """Syntax errors must not propagate — pipeline must not stall."""
        record = compute_complexity("src/bad.py", SYNTAX_ERROR_SOURCE)
        assert record is not None

    def test_multi_param_avoids_docstring_false_positives(self):
        """mi_visit(multi=True) should not penalise well-documented code."""
        well_documented = '"""\nModule docstring.\n"""\n\ndef add(a, b):\n    """Add two numbers."""\n    return a + b\n'
        record = compute_complexity("src/docs.py", well_documented)
        assert record.maintainability > 50.0

    def test_class_methods_counted(self):
        cls_source = "class Foo:\n    def bar(self):\n        if True:\n            pass\n"
        record = compute_complexity("src/foo.py", cls_source)
        assert record.function_count >= 1

    def test_unicode_source_handled(self):
        unicode_src = "# -*- coding: utf-8 -*-\ndef greet():\n    return '日本語'\n"
        record = compute_complexity("src/unicode.py", unicode_src)
        assert not record.parse_error


# ── Router Integration Tests ─────────────────────────────────────────────────

class TestComplexityRouter:

    def test_missing_run_id_returns_424_or_uses_latest(self, client, mock_complexity_service):
        resp = client.post("/api/v1/analyze/complexity", json={})
        assert resp.status_code in (200, 424)

    def test_unknown_run_id_returns_404(self, client):
        resp = client.post("/api/v1/analyze/complexity", json={"run_id": "nonexistent"})
        assert resp.status_code == 404

    def test_valid_run_id_returns_200(self, client, seeded_run_context, mock_complexity_service):
        resp = client.post(
            "/api/v1/analyze/complexity",
            json={"run_id": seeded_run_context.run_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "complexity" in body
        for item in body["complexity"]:
            assert "file" in item
            assert "complexity" in item
            assert "maintainability" in item

    def test_parse_error_files_appear_with_sentinel(
        self, client, seeded_run_context_with_bad_file, mock_complexity_partial_error
    ):
        resp = client.post(
            "/api/v1/analyze/complexity",
            json={"run_id": seeded_run_context_with_bad_file.run_id},
        )
        assert resp.status_code == 200
        errors = [i for i in resp.json()["complexity"] if i["parse_error"]]
        assert len(errors) >= 1
        assert errors[0]["complexity"] == -1.0

    def test_empty_analysis_returns_424(self, client, empty_run_context):
        resp = client.post(
            "/api/v1/analyze/complexity",
            json={"run_id": empty_run_context.run_id},
        )
        assert resp.status_code == 424


# ── Context Propagation Tests ────────────────────────────────────────────────

class TestComplexityContextPropagation:

    @pytest.mark.asyncio
    async def test_complexity_checkpointed_in_context(
        self, run_context, context_store, mocker
    ):
        """
        After run_complexity(), RunContext.complexity is populated and
        the checkpoint is written to the store.
        """
        from app.modules.complexity.service import run_complexity
        # Patch file reading to avoid needing actual cloned files
        mocker.patch(
            "app.modules.complexity.service.read_source",
            return_value=SIMPLE_SOURCE,
        )
        await run_complexity(run_context, context_store)
        stored = await context_store.get(run_context.run_id)
        assert stored.stage_flags["complexity"].value == "COMPLETE"
        assert len(stored.complexity) == len(stored.analysis)

    @pytest.mark.asyncio
    async def test_complexity_records_available_for_risk_module(
        self, run_context
    ):
        """
        run_context.complexity populated here is available to
        test_03_risk.py (session fixture propagation).
        """
        assert len(run_context.complexity) > 0, (
            "Complexity records should have been set by "
            "TestComplexityContextPropagation.test_complexity_checkpointed_in_context"
        )

    @pytest.mark.asyncio
    async def test_analysis_prerequisite_enforced(
        self, empty_run_context, context_store
    ):
        """run_complexity raises ComplexityError when analysis is empty."""
        from app.modules.complexity.service import run_complexity
        from app.core.exceptions import ComplexityError
        with pytest.raises(ComplexityError):
            await run_complexity(empty_run_context, context_store)


# ── Schema Validation Tests ───────────────────────────────────────────────────

class TestComplexityRecordSchema:

    def test_negative_complexity_rejected(self):
        with pytest.raises(Exception):
            ComplexityRecord(
                file="x.py", complexity=-5.0, maintainability=60.0, function_count=1
            )

    def test_sentinel_minus_one_allowed(self):
        r = ComplexityRecord(
            file="x.py", complexity=-1.0, maintainability=-1.0,
            function_count=0, parse_error=True
        )
        assert r.complexity == -1.0

    def test_maintainability_above_100_rejected(self):
        with pytest.raises(Exception):
            ComplexityRecord(
                file="x.py", complexity=5.0, maintainability=101.0, function_count=2
            )
```

---

## Running Tests for This Module

```bash
# Isolated run
pytest tests/test_02_complexity.py -v

# With context propagation from analyzer
pytest tests/test_01_analyzer.py tests/test_02_complexity.py -v

# Coverage
pytest tests/test_02_complexity.py --cov=app/modules/complexity --cov-report=term-missing
```

---

## Common Issues & Resolutions

**Issue:** `radon.complexity.cc_visit` returns an empty list on valid Python files.
**Resolution:** This occurs on files that contain only module-level statements (no functions or
classes). `complexity = 0.0` is the correct result and should not be treated as an error.

**Issue:** `mi_visit` returns a value slightly outside [0, 100] for some edge cases.
**Resolution:** Clamp the returned value: `mi_score = max(0.0, min(100.0, mi_visit(...)))`.

**Issue:** Files with very long lines cause Radon to exceed recursion limits.
**Resolution:** Wrap `cc_visit` in a `sys.setrecursionlimit(5000)` context or split the source
into smaller chunks and aggregate.

**Issue:** Docstrings counted as complexity.
**Resolution:** Always pass `multi=True` to `mi_visit`. This enables multi-line string handling
that correctly ignores docstring content in complexity calculations.
